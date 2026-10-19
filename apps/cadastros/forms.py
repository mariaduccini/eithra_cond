"""Formulários dos cadastros básicos operacionais.

Cada formulário pede apenas o que o cadastro precisa: nome e tipo no local,
nome no equipamento, nome no fornecedor. O resto é opcional e pode ser
completado aos poucos.

Os campos são os do próprio cadastro. Assim a validação é a mesma do domínio —
o modelo recusa o que não faz sentido — e não existe uma segunda lista de
regras no formulário para divergir com o tempo.
"""
from django import forms

from apps.condominios.models import Condominio

from .constants import TipoLocal, TipoPessoa
from .models import Equipamento, Fornecedor, LocalOperacional

# Recorte de entrada de texto, repetido em quase todo campo. Aparência é
# assunto do tema Sneat: aqui só se diz qual classe de campo usar.
TEXTO = {"class": "form-control"}
SELECAO = {"class": "form-select"}


class CondominioForm(forms.ModelForm):
    """O cadastro do condomínio: dados gerais, endereço e administração.

    Todos os campos administrativos são opcionais de propósito. Um condomínio
    que já existe continua válido sem eles, e a gestão completa o cadastro aos
    poucos — só o nome é obrigatório.

    Em modo de leitura (``somente_leitura``) os campos aparecem desabilitados:
    a supervisão vê o mesmo cadastro que a gestão, sem poder alterá-lo.
    """

    class Meta:
        model = Condominio
        fields = (
            "nome",
            "cnpj",
            "logradouro",
            "numero",
            "complemento",
            "bairro",
            "cidade",
            "uf",
            "cep",
            "telefone_administrativo",
            "email_administrativo",
            "quantidade_unidades",
            "observacao_administrativa",
        )
        widgets = {
            "nome": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "cnpj": forms.TextInput(
                attrs={**TEXTO, "placeholder": "00.000.000/0000-00", "autocomplete": "off"}
            ),
            "logradouro": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "numero": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "complemento": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "bairro": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "cidade": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "uf": forms.TextInput(
                attrs={**TEXTO, "maxlength": 2, "placeholder": "UF", "autocomplete": "off"}
            ),
            "cep": forms.TextInput(
                attrs={**TEXTO, "placeholder": "00000-000", "autocomplete": "off"}
            ),
            "telefone_administrativo": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "email_administrativo": forms.EmailInput(attrs={**TEXTO, "autocomplete": "off"}),
            "quantidade_unidades": forms.NumberInput(
                attrs={**TEXTO, "min": 0, "step": 1, "inputmode": "numeric"}
            ),
            "observacao_administrativa": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que a gestão precisa lembrar deste condomínio"}
            ),
        }

    def __init__(self, *args, somente_leitura=False, **kwargs):
        super().__init__(*args, **kwargs)
        if somente_leitura:
            for campo in self.fields.values():
                campo.disabled = True


class LocalForm(forms.ModelForm):
    """Cadastro de um lugar do condomínio."""

    class Meta:
        model = LocalOperacional
        fields = ("nome", "tipo", "descricao")
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Bloco A, Garagem subsolo, Elevador social",
                    "autocomplete": "off",
                }
            ),
            "tipo": forms.Select(attrs=SELECAO, choices=TipoLocal.choices),
            "descricao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "Detalhe opcional deste lugar"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Não classificar o lugar é uma resposta legítima: quem não escolhe fica
        # com "Outro", o mesmo padrão do domínio. Sem isto, o formulário
        # obrigaria a escolher e já viria apontando para a primeira opção.
        self.fields["tipo"].required = False
        self.fields["tipo"].choices = [("", "Outro")] + list(TipoLocal.choices)

    def clean_tipo(self):
        return self.cleaned_data.get("tipo") or TipoLocal.OUTRO


class EquipamentoForm(forms.ModelForm):
    """Cadastro de um equipamento do condomínio.

    O local só oferece opções do próprio condomínio: a lista vem do vínculo da
    pessoa com o condomínio em que ela está trabalhando, e nunca do navegador.
    """

    class Meta:
        model = Equipamento
        fields = (
            "nome",
            "tipo_texto",
            "local",
            "fabricante",
            "modelo",
            "numero_serie",
            "patrimonio",
            "observacao",
        )
        widgets = {
            "nome": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Bomba de recalque, Portão da garagem",
                    "autocomplete": "off",
                }
            ),
            "tipo_texto": forms.TextInput(
                attrs={**TEXTO, "placeholder": "Bomba, portão, elevador", "autocomplete": "off"}
            ),
            "local": forms.Select(attrs=SELECAO),
            "fabricante": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "modelo": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "numero_serie": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "patrimonio": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe sobre o equipamento"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["local"].queryset = LocalOperacional.objects.ativos().do_condominio(
            condominio
        )
        self.fields["local"].empty_label = "Sem local definido"


class FornecedorForm(forms.ModelForm):
    """Cadastro de quem atende o condomínio."""

    class Meta:
        model = Fornecedor
        fields = (
            "nome_razao_social",
            "nome_fantasia",
            "tipo_pessoa",
            "documento",
            "email",
            "telefone",
            "contato",
            "observacao",
        )
        widgets = {
            "nome_razao_social": forms.TextInput(
                attrs={
                    **TEXTO,
                    "placeholder": "Ex.: Elevadores Silva Ltda.",
                    "autocomplete": "off",
                }
            ),
            "nome_fantasia": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "tipo_pessoa": forms.Select(attrs=SELECAO, choices=TipoPessoa.choices),
            "documento": forms.TextInput(
                attrs={**TEXTO, "placeholder": "CPF ou CNPJ", "autocomplete": "off"}
            ),
            "email": forms.EmailInput(attrs={**TEXTO, "autocomplete": "off"}),
            "telefone": forms.TextInput(
                attrs={**TEXTO, "placeholder": "(00) 00000-0000", "autocomplete": "off"}
            ),
            "contato": forms.TextInput(attrs={**TEXTO, "autocomplete": "off"}),
            "observacao": forms.Textarea(
                attrs={**TEXTO, "rows": 3, "placeholder": "O que se sabe sobre o fornecedor"}
            ),
        }

    def __init__(self, *args, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Não saber se é pessoa física ou jurídica é comum no primeiro contato:
        # quem não escolhe fica com "Não informado", o padrão do domínio. Sem
        # isto, o formulário obrigaria a escolher e já viria apontando para
        # "Pessoa física".
        self.fields["tipo_pessoa"].required = False
        self.fields["tipo_pessoa"].choices = [("", "Não informado")] + list(
            TipoPessoa.choices
        )
        # O condomínio não é campo do formulário — quem decide é o vínculo da
        # pessoa, na view. Mas o cadastro precisa saber a que condomínio
        # pertence para o domínio recusar, na própria validação, um documento
        # que já existe ali. Sem isso, a regra só apareceria como erro do banco.
        if condominio is not None:
            self.instance.condominio = condominio

    def clean_tipo_pessoa(self):
        return self.cleaned_data.get("tipo_pessoa") or TipoPessoa.NAO_INFORMADO
