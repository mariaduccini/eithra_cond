"""Formulários do domínio de demandas.

Pede-se apenas o necessário. A pergunta principal é uma só — o que aconteceu —,
e o que der para ler desse relato já vem preenchido: assunto, prioridade, prazo
e local. O que não der continua sendo pergunta, com ajuste à mão.

O responsável sai de uma lista fechada: só quem tem vínculo ativo com o
condomínio e enxerga o módulo pode ser escolhido. Como a lista é a mesma que o
domínio aceita, um responsável de outro condomínio é recusado pelo próprio
formulário, e não por uma checagem solta na view.
"""
from django import forms
from django.db.models import Q

from apps.accounts.models import User
from apps.cadastros.models import LocalOperacional
from apps.condominios.models import Condominio

from .constants import PrioridadeDemanda, StatusDemanda
from .derivacao import Leitura, interpretar, local_do_relato
from .permissoes import usuarios_elegiveis_como_responsavel
from .services import LIMITE_DA_OBSERVACAO


def _linha(valor: str | None) -> str:
    """Uma linha só: sem espaços repetidos e sem espaço nas pontas."""
    return " ".join((valor or "").split())


def _locais_do_condominio(condominio, atual=None):
    """Os locais oferecidos numa escolha.

    Só os que estão em uso — mais aquele que a demanda já aponta, para que um
    cadastro desativado depois continue legível sem voltar a ser oferecido.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return LocalOperacional.objects.none()

    locais = LocalOperacional.objects.do_condominio(condominio)
    if atual is not None and getattr(atual, "pk", None):
        return locais.filter(Q(ativo=True) | Q(pk=atual.pk)).order_by("nome")
    return locais.ativos().order_by("nome")


def _locais_entre(condominios):
    """Os locais elegíveis entre os condomínios em vista.

    Serve para o filtro da listagem. A escolha continua sendo real: só os locais
    ativos dos condomínios que a pessoa pode consultar entram na lista.
    """
    if condominios is None:
        return LocalOperacional.objects.none()
    return LocalOperacional.objects.filter(
        condominio__in=condominios,
        ativo=True,
    ).order_by("nome")


# A ordem em que as situações aparecem para escolha, do começo ao fim do ciclo.
ORDEM_DOS_STATUS = (
    StatusDemanda.ABERTA,
    StatusDemanda.EM_ANDAMENTO,
    StatusDemanda.AGUARDANDO,
    StatusDemanda.CONCLUIDA,
    StatusDemanda.CANCELADA,
)

# Recortes de prazo que a lista oferece. Não é filtro de calendário: é a leitura
# que decide o que fazer primeiro.
FILTROS_DE_PRAZO = (
    ("", "Qualquer prazo"),
    ("vencidas", "Vencidas"),
    ("hoje", "Vencem hoje"),
    ("proximas", "Vencem em 7 dias"),
    ("sem_prazo", "Sem prazo"),
)


class DemandaForm(forms.Form):
    """Cria ou edita o conteúdo de uma demanda.

    O conjunto de condomínios possíveis vem do vínculo ativo da pessoa, e nunca
    do navegador. Quando há apenas um, o campo continua sendo validado, mas não
    é pedido de novo. Ao editar, o condomínio é fixo: demanda não muda de casa.

    Nada aqui é obrigatório além do relato. O que a pessoa não escolher é lido
    do que ela escreveu — e, quando a leitura não sustenta nada, o padrão do
    domínio responde, sem preencher campo por conta própria.
    """

    condominio = forms.ModelChoiceField(
        label="Condomínio",
        queryset=Condominio.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    relato = forms.CharField(
        label="O que aconteceu?",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Escreva com as palavras do dia a dia o que aconteceu.",
            }
        ),
    )
    titulo = forms.CharField(
        label="Assunto",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Aparece a partir do relato",
                "autocomplete": "off",
            }
        ),
    )
    descricao = forms.CharField(
        label="Descrição",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=PrioridadeDemanda.choices,
        required=False,
        initial=PrioridadeDemanda.NORMAL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Quem acompanha?",
        queryset=User.objects.none(),
        required=False,
        empty_label="Ninguém ainda",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    prazo = forms.DateField(
        label="Prazo",
        required=False,
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date"},
            format="%Y-%m-%d",
        ),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Sem local definido",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    # O lugar é uma busca, não uma lista longa: quem escreve o nome de um local
    # do condomínio encontra o cadastro; quem escreve outro nome continua sendo
    # entendido, porque o texto fica guardado como o que se sabe do lugar.
    local_texto = forms.CharField(
        label="Onde aconteceu?",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Buscar ou digitar o lugar",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, condominios=None, condominio=None, **kwargs):
        super().__init__(*args, **kwargs)

        # O que a leitura do relato sustentar neste envio. Começa vazio: sem
        # relato não há o que ter lido.
        self._leitura = Leitura()

        permitidos = condominios if condominios is not None else Condominio.objects.none()
        campo_condominio = self.fields["condominio"]

        if condominio is not None:
            # Edição: a demanda pertence a este condomínio e não muda de casa.
            # Como a casa já está decidida aqui, o campo não volta a ser
            # perguntado nem exigido do navegador — a tela de edição não o
            # desenha, e cobrar o que não é perguntado recusaria toda edição.
            campo_condominio.queryset = Condominio.objects.filter(pk=condominio.pk)
            campo_condominio.initial = condominio.pk
            campo_condominio.required = False
            campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = condominio
        else:
            campo_condominio.queryset = permitidos
            elegiveis = list(permitidos)
            if len(elegiveis) == 1:
                campo_condominio.initial = elegiveis[0].pk
                campo_condominio.widget = forms.HiddenInput()
            self.condominio_efetivo = self._condominio_escolhido(permitidos)

        self.fields["responsavel"].queryset = usuarios_elegiveis_como_responsavel(
            self.condominio_efetivo
        )
        self.fields["local"].queryset = _locais_do_condominio(
            self.condominio_efetivo, self._local_atual()
        )
        self.fields["local_texto"].widget.attrs["list"] = (
            f"{self['local_texto'].id_for_label}_lista"
        )

    def _local_atual(self):
        """O local que a demanda já aponta, quando o formulário é de edição.

        Serve só para manter o cadastro atual entre as opções, mesmo que tenha
        sido desativado depois. Não é validação.
        """
        local_id = (self.initial or {}).get("local")
        if not local_id:
            return None
        return LocalOperacional.objects.filter(pk=local_id).first()

    def _condominio_escolhido(self, permitidos):
        """Qual condomínio vale para montar a lista de responsáveis e de locais.

        Vale o que a pessoa enviou, desde que esteja entre os permitidos; senão,
        o que já se sabe do contexto e, na falta dele, o único elegível. Serve
        apenas para montar as opções — a validação continua sendo a do campo,
        contra a mesma lista.
        """
        if self.is_bound:
            enviado = self.data.get("condominio")
        else:
            enviado = (self.initial or {}).get("condominio")
        if enviado:
            escolhido = permitidos.filter(pk=enviado).first()
            if escolhido is not None:
                return escolhido

        elegiveis = list(permitidos)
        return elegiveis[0] if len(elegiveis) == 1 else None

    @property
    def leitura(self) -> Leitura:
        """O que a leitura do relato sustentou neste envio.

        Montada no ``clean()``, sobre o mesmo texto que fecha o assunto e o
        peso da demanda — é ela que sustenta o que a revisão mostra antes de
        registrar, com o motivo de cada proposta.
        """
        return self._leitura

    def responsavel_sugerido(self, usuario):
        """Quem acompanha, quando o próprio contexto já responde.

        Quem registra costuma ser quem acompanha o que registrou — desde que
        tenha vínculo que o permita. Fora disso não há sugestão: apontar
        alguém sem base seria decidir no lugar de quem registra.
        """
        if usuario is None or not getattr(usuario, "pk", None):
            return None
        if self.fields["responsavel"].queryset.filter(pk=usuario.pk).exists():
            return usuario
        return None

    def clean_titulo(self):
        return _linha(self.cleaned_data["titulo"])

    def clean_descricao(self):
        return (self.cleaned_data["descricao"] or "").strip()

    def clean(self):
        """Fecha o que o relato já disse, sem pedir a mesma coisa duas vezes.

        O que a pessoa escolheu vale. O que ela não escolheu é lido do relato e,
        se a leitura não sustentar nada, fica com o padrão do domínio.
        """
        dados = super().clean()

        relato = _linha(dados.get("relato"))
        descricao = dados.get("descricao") or ""

        # O relato é a descrição: pedir os dois textos seria pedir duas vezes o
        # mesmo, e o que foi escrito por extenso é o que vale.
        if relato and not descricao:
            descricao = relato
            dados["descricao"] = descricao

        # Uma leitura só do que foi escrito: o assunto, o peso e o prazo saem
        # dela, e é ela que sustenta o que a revisão mostra.
        self._leitura = interpretar(
            descricao or relato, condominio=self.condominio_efetivo
        )

        assunto = dados.get("titulo") or self._leitura.assunto
        dados["titulo"] = assunto
        if not assunto:
            self.add_error(
                "relato", "Escreva o que aconteceu para registrar a demanda."
            )
            return dados

        if not dados.get("prioridade"):
            dados["prioridade"] = self._leitura.prioridade

        self._resolver_local(dados)
        return dados

    def _resolver_local(self, dados):
        """Transforma o lugar digitado no cadastro do condomínio, ou em texto.

        Nome que existe no cadastro vira vínculo; nome que não existe continua
        legível como o que se sabe do lugar. Sem o campo na tela, o texto legado
        da demanda antiga fica intacto.
        """
        if "local_texto" not in self.data:
            dados["local_contexto"] = None
            return

        texto = _linha(dados.get("local_texto"))
        if not texto:
            dados["local"] = None
            dados["local_contexto"] = ""
            return

        local = local_do_relato(texto, self.condominio_efetivo)
        dados["local"] = local
        dados["local_contexto"] = "" if local else texto



class AlteracaoDeStatusForm(forms.Form):
    """Move a demanda para outra situação entre as que esta pessoa alcança."""

    status = forms.ChoiceField(
        label="Nova situação",
        choices=(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, permitidos=(), **kwargs):
        super().__init__(*args, **kwargs)
        alcancaveis = set(permitidos)
        self.fields["status"].choices = [
            (situacao, StatusDemanda(situacao).label)
            for situacao in ORDEM_DOS_STATUS
            if situacao in alcancaveis
        ]


class ObservacaoDaDemandaForm(forms.Form):
    """Registra o que se sabe sobre a demanda, sem mexer em nenhum campo dela."""

    texto = forms.CharField(
        label="Observação",
        max_length=LIMITE_DA_OBSERVACAO,
        error_messages={"required": "Escreva a observação antes de registrar."},
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "O que se sabe agora sobre esta demanda",
            }
        ),
    )

    def clean_texto(self):
        texto = " ".join(self.cleaned_data["texto"].split())
        if not texto:
            raise forms.ValidationError("Escreva a observação antes de registrar.")
        return texto


class FiltroDeDemandasForm(forms.Form):
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
    prioridade = forms.ChoiceField(
        label="Prioridade",
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    local = forms.ModelChoiceField(
        label="Local",
        queryset=LocalOperacional.objects.none(),
        required=False,
        empty_label="Todos os locais",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    responsavel = forms.ModelChoiceField(
        label="Responsável",
        queryset=None,
        required=False,
        empty_label="Todos os responsáveis",
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    prazo = forms.ChoiceField(
        label="Prazo",
        choices=FILTROS_DE_PRAZO,
        required=False,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    busca = forms.CharField(
        label="Buscar",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Título, descrição ou local",
                "autocomplete": "off",
            }
        ),
    )
    minhas = forms.BooleanField(
        label="Só as minhas",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, condominios=None, responsaveis=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["condominio"].queryset = (
            condominios if condominios is not None else Condominio.objects.none()
        )
        self.fields["status"].choices = [("", "Todas as situações")] + [
            (situacao, StatusDemanda(situacao).label) for situacao in ORDEM_DOS_STATUS
        ]
        self.fields["prioridade"].choices = [("", "Todas as prioridades")] + list(
            PrioridadeDemanda.choices
        )
        self.fields["local"].queryset = _locais_entre(condominios)
        self.fields["responsavel"].queryset = (
            responsaveis if responsaveis is not None else User.objects.none()
        )

