"""Formulários do domínio de manutenções operacionais.

Pede-se apenas o necessário: título e condomínio são o mínimo para a manutenção
existir, e o resto aparece em progressive disclosure — tipo, problema, local,
equipamento, fornecedor, responsável, previsão e custo. Nada de campo técnico na
tela.

A prioridade não é campo: ela é o que o problema relatado sustenta, lida pela
mesma regra que o resto do sistema usa. Quem registra corrige o fato, e o peso
se recalcula.

As listas de escolha são fechadas e vêm do condomínio da manutenção: local,
equipamento, fornecedor e responsável são os dele, e de mais nenhum. Como a
lista é a mesma que o domínio aceita, um registro de outro condomínio é recusado
pelo próprio formulário, e não por uma checagem solta na tela.
"""
from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.models import Condominio
from apps.core.constants import Prioridade
from apps.demandas.derivacao import prioridade_do_relato

from .constants import (
    ACAO_DE_REABERTURA,
    ACOES_DE_STATUS,
    StatusManutencao,
    TipoManutencao,
)
from .permissoes import usuarios_elegiveis_como_responsavel

# A ordem em que as situações aparecem para escolha, do começo ao fim do ciclo.
ORDEM_DOS_STATUS = (
    StatusManutencao.ABERTA,
    StatusManutencao.PROGRAMADA,
    StatusManutencao.EM_ANDAMENTO,
    StatusManutencao.AGUARDANDO,
    StatusManutencao.CONCLUIDA,
    StatusManutencao.CANCELADA,
)

# Recortes de previsão que a lista oferece. Não é filtro de calendário: é a
# leitura que decide o que fazer primeiro.
FILTROS_DE_PREVISAO = (
    ("", "Qualquer previsão"),
    ("atrasadas", "Atrasadas"),
    ("hoje", "Previstas para hoje"),
    ("proximas", "Próximos 7 dias"),
    ("sem_previsao", "Sem previsão"),
)


def _escolhas_do_condominio(modelo, condominio, atual=None):
    """Os registros do condomínio oferecidos numa escolha.

    Só os que estão em uso — mais aquele que a manutenção já aponta, para que um
    cadastro desativado depois continue legível sem voltar a ser oferecido.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return modelo.objects.none()

    queryset = modelo.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        return queryset.filter(Q(ativo=True) | Q(pk=atual.pk))
    return queryset.ativos()


class ManutencaoForm(forms.Form):
    """Cria ou edita o conteúdo de uma manutenção.

    O conjunto de condomínios possíveis vem do vínculo ativo da pessoa, e nunca
    do navegador. Ao editar, o condomínio é fixo: manutenção não muda de casa.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=TipoManutencao.choices,
        initial=TipoManutencao.CORRETIVA,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    titulo = forms.CharField(
        label="Título",
        max_length=200,
        error_messages={"required": "Escreva o que precisa ser feito."},
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ex.: Troca da bomba do reservatório",
                "autocomplete": "off",
            }
        ),
    )
    descricao_problema = forms.CharField(
        label="Problema",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que está acontecendo e motivou a manutenção",
            }
        ),
    )
    descricao_servico = forms.CharField(
        label="Serviço realizado",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que foi feito. Obrigatório para concluir.",
            }
        ),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Sem local",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    equipamento = forms.ModelChoiceField(
        label="Equipamento",
        queryset=Equipamento.objects.none(),
        required=False,
        empty_label="Sem equipamento",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    fornecedor = forms.ModelChoiceField(
        label="Fornecedor",
        queryset=Fornecedor.objects.none(),
        required=False,
        empty_label="Sem fornecedor",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Sem responsável",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    data_prevista = forms.DateField(
        label="Data prevista",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
    )
    custo_previsto = forms.DecimalField(
        label="Custo previsto",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        error_messages={"min_value": "O custo previsto não pode ser negativo."},
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "min": "0"}
        ),
    )
    custo_real = forms.DecimalField(
        label="Custo real",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        error_messages={"min_value": "O custo real não pode ser negativo."},
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "min": "0"}
        ),
    )

    def __init__(
        self,
        *args,
        condominios=None,
        condominio=None,
        manutencao=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            # Edição ou criação dentro de um condomínio já resolvido: não muda de casa.
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
        escolhido = getattr(manutencao, "pk", None) and manutencao or None

        self.fields["local"].queryset = _escolhas_do_condominio(
            LocalOperacional, atual, getattr(escolhido, "local", None)
        )
        self.fields["equipamento"].queryset = _escolhas_do_condominio(
            Equipamento, atual, getattr(escolhido, "equipamento", None)
        )
        self.fields["fornecedor"].queryset = _escolhas_do_condominio(
            Fornecedor, atual, getattr(escolhido, "fornecedor", None)
        )
        self.fields["responsavel"].queryset = usuarios_elegiveis_como_responsavel(atual)

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar as listas de escolha.

        Vale o que a pessoa enviou, desde que esteja entre os permitidos; senão,
        o único elegível, quando houver só um. Serve apenas para montar as
        opções — a validação continua sendo a do campo, contra a mesma lista.
        """
        enviado = self.data.get("condominio") if self.is_bound else None
        if enviado:
            escolhido = permitidos.filter(pk=enviado).first()
            if escolhido is not None:
                return escolhido

        elegiveis = list(permitidos)
        return elegiveis[0] if len(elegiveis) == 1 else None

    def clean_titulo(self):
        titulo = " ".join(self.cleaned_data["titulo"].split())
        if not titulo:
            raise forms.ValidationError("Escreva o que precisa ser feito.")
        return titulo

    def clean_descricao_problema(self):
        return (self.cleaned_data["descricao_problema"] or "").strip()

    def clean_descricao_servico(self):
        return (self.cleaned_data["descricao_servico"] or "").strip()

    def clean(self):
        """Recusa incoerências que só aparecem juntando dois campos.

        O equipamento e o local precisam concordar: um equipamento que fica em
        um lugar não é atendido em outro sem que alguém diga isso. Divergência
        aqui é recusada com o nome do lugar certo — nunca corrigida em silêncio.
        """
        dados = super().clean()

        # O peso não se escolhe: ele é o que o problema relatado sustenta, lido
        # pela mesma regra que o resto do sistema usa. Corrigir o texto é o que
        # muda a prioridade — por isso ela nunca vem do navegador.
        dados["prioridade"], _ = prioridade_do_relato(
            dados.get("descricao_problema") or dados.get("titulo")
        )

        equipamento = dados.get("equipamento")
        local = dados.get("local")

        if equipamento is None or local is None:
            return dados

        local_do_equipamento = equipamento.local
        if local_do_equipamento is not None and local_do_equipamento.pk != local.pk:
            self.add_error(
                "local",
                f"O equipamento escolhido fica em {local_do_equipamento.nome}. "
                "Escolha o mesmo local ou troque o equipamento.",
            )
        return dados


def acoes_de_status(permitidos, manutencao=None):
    """As ações que esta manutenção alcança agora, com o verbo que a tela mostra.

    O valor de cada ação é a situação que ela produz — e é por isso que a tela
    oferece o verbo, e não o nome do estado. Concluir e cancelar não entram: cada
    uma tem ação própria, porque pedem, respectivamente, o que foi feito e por
    quê. A manutenção encerrada não anda por movimento comum: a única ação é
    devolvê-la à operação. Nas demais, não se oferece a ação que levaria a
    manutenção aonde ela já está.
    """
    alcancaveis = set(permitidos)
    if manutencao is not None and getattr(manutencao, "esta_encerrada", False):
        return tuple(acao for acao in (ACAO_DE_REABERTURA,) if acao[0] in alcancaveis)
    return tuple(
        (situacao, verbo)
        for situacao, verbo in ACOES_DE_STATUS
        if situacao in alcancaveis and situacao != getattr(manutencao, "status", None)
    )


class AcaoDeStatusForm(forms.Form):
    """Registra a ação que move a manutenção — nunca a situação escolhida.

    Cada verbo leva a uma situação: "Iniciar" põe a manutenção em andamento,
    "Aguardar" a deixa dependendo de terceiro, "Programar" marca o serviço com
    data. Concluir e cancelar ficam de fora: têm ação própria. Quem registra diz
    o que aconteceu; a situação é consequência. O backend só aceita uma ação que
    a pessoa alcança — qualquer outra é recusada, venha de onde vier.
    """

    acao = forms.ChoiceField(
        label="O que aconteceu?",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, permitidos=(), manutencao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["acao"].choices = acoes_de_status(permitidos, manutencao)


class ConclusaoDeManutencaoForm(forms.Form):
    """Conclui a manutenção dizendo o que foi feito e quando.

    Concluir não é só trocar de situação: exige a descrição do serviço. A data
    vem preenchida com hoje, mas pode recuar até o dia em que o serviço de fato
    aconteceu; em branco, vale o momento do registro. O custo real pode
    continuar vazio — nem todo serviço fecha valor ao terminar.

    A data é a do serviço, não a do registro: uma preventiva feita em setembro
    pode ser lançada agora, e o que fica guardado é o dia em que ela aconteceu.
    O futuro é recusado porque ainda não aconteceu.
    """

    data_conclusao = forms.DateField(
        label="Data da conclusão",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        initial=timezone.localdate,
        error_messages={
            "invalid": "Informe uma data válida, no formato dd/mm/aaaa.",
        },
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
        ),
    )
    descricao_servico = forms.CharField(
        label="Serviço realizado",
        error_messages={
            "required": "Descreva o serviço realizado antes de concluir a manutenção."
        },
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que foi feito nesta manutenção",
            }
        ),
    )
    custo_real = forms.DecimalField(
        label="Custo real",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        error_messages={"min_value": "O custo real não pode ser negativo."},
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "min": "0"}
        ),
    )

    def clean_data_conclusao(self):
        data = self.cleaned_data["data_conclusao"]
        if data is not None and data > timezone.localdate():
            raise forms.ValidationError(
                "A data da conclusão não pode estar no futuro."
            )
        return data

    def clean_descricao_servico(self):
        servico = (self.cleaned_data["descricao_servico"] or "").strip()
        if not servico:
            raise forms.ValidationError(
                "Descreva o serviço realizado antes de concluir a manutenção."
            )
        return servico


class CancelamentoDeManutencaoForm(forms.Form):
    """Encerra a manutenção sem que o serviço aconteça.

    O motivo é opcional: cancelar continua sendo uma decisão válida mesmo quando
    não há o que explicar. Quando houver, fica guardado no histórico.
    """

    motivo = forms.CharField(
        label="Motivo",
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Por que esta manutenção não vai acontecer",
                "autocomplete": "off",
            }
        ),
    )

    def clean_motivo(self):
        return " ".join((self.cleaned_data["motivo"] or "").split())


class FiltroDeManutencoesForm(forms.Form):
    """Filtros essenciais da lista. Nunca obrigatórios: filtrar é opcional."""

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
    tipo = forms.ChoiceField(
        label="Tipo",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=User.objects.none(),
        required=False,
        empty_label="Todos os responsáveis",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Todos os locais",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    equipamento = forms.ModelChoiceField(
        label="Equipamento",
        queryset=Equipamento.objects.none(),
        required=False,
        empty_label="Todos os equipamentos",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    fornecedor = forms.ModelChoiceField(
        label="Fornecedor",
        queryset=Fornecedor.objects.none(),
        required=False,
        empty_label="Todos os fornecedores",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    previsao = forms.ChoiceField(
        label="Previsão",
        choices=FILTROS_DE_PREVISAO,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Título, problema ou serviço",
                "autocomplete": "off",
            }
        ),
    )
    minhas = forms.BooleanField(
        label="Só as minhas",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(
        self,
        *args,
        condominios=None,
        responsaveis=None,
        locais=None,
        equipamentos=None,
        fornecedores=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["status"].choices = [("", "Todas as situações")] + [
            (situacao, StatusManutencao(situacao).label) for situacao in ORDEM_DOS_STATUS
        ]
        self.fields["tipo"].choices = [("", "Todos os tipos")] + list(
            TipoManutencao.choices
        )
        self.fields["prioridade"].choices = [("", "Todas as prioridades")] + list(
            Prioridade.choices
        )
        self.fields["responsavel"].queryset = (
            responsaveis if responsaveis is not None else User.objects.none()
        )
        self.fields["local"].queryset = (
            locais if locais is not None else LocalOperacional.objects.none()
        )
        self.fields["equipamento"].queryset = (
            equipamentos if equipamentos is not None else Equipamento.objects.none()
        )
        self.fields["fornecedor"].queryset = (
            fornecedores if fornecedores is not None else Fornecedor.objects.none()
        )
