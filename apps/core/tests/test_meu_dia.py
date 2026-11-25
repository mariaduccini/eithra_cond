"""O "Meu dia": o que quem conduz a operação precisa fazer hoje.

A tela reúne três origens que já sabem se responder — a ocorrência de rotina, a
manutenção e a demanda — e não decide nada sozinha. Estes testes travam o
essencial: cada pessoa vê só os itens dela, o condomínio em vista isola o que
aparece, o atrasado vem antes do de hoje, o vazio tem a sua mensagem e o item de
menu existe só para quem conduz o dia.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.models import Demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.manutencoes.models import Manutencao
from apps.rotinas.models import ExecucaoRotina, RotinaRecorrente


class BaseDoMeuDiaTests(TestCase):
    """Um condomínio, a gestão que cria e uma operação que executa."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.operador = criar_pessoa("operador@exemplo.test", "Operador")
        criar_vinculo(self.operador, self.condominio, Papel.OPERACIONAL)
        self.colega = criar_pessoa("colega@exemplo.test", "Colega")
        criar_vinculo(self.colega, self.condominio, Papel.OPERACIONAL)

    def _manutencao(self, responsavel, titulo, data_prevista=None, **campos):
        return Manutencao.objects.create(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=titulo,
            responsavel=responsavel,
            data_prevista=data_prevista,
            **campos,
        )

    def _demanda(self, titulo, **campos):
        return Demanda.objects.create(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=titulo,
            **campos,
        )

    def _execucao(self, responsavel, titulo, data_prevista):
        rotina = RotinaRecorrente.objects.create(
            condominio=self.condominio,
            titulo=titulo,
            data_inicio=self.hoje - timedelta(days=30),
            criado_por=self.gestao,
        )
        return ExecucaoRotina.objects.create(
            rotina=rotina,
            condominio=self.condominio,
            data_prevista=data_prevista,
            responsavel=responsavel,
        )

    def pagina(self, usuario, **parametros):
        self.client.force_login(usuario)
        return self.client.get(reverse("core:meu_dia"), parametros or None)


class FiltroPorPessoaTests(BaseDoMeuDiaTests):
    """O "meu dia" é de cada um: só entra o que está sob a própria pessoa."""

    def test_nao_mostra_item_de_outra_pessoa(self):
        self._manutencao(self.operador, "Ajustar portão", data_prevista=self.hoje)
        self._manutencao(self.colega, "Pintar o muro", data_prevista=self.hoje)
        resposta = self.pagina(self.operador)
        self.assertContains(resposta, "Ajustar portão")
        self.assertNotContains(resposta, "Pintar o muro")

    def test_mostra_rotina_e_demanda_minhas(self):
        self._execucao(self.operador, "Conferir bomba", self.hoje)
        self._demanda("Trocar lâmpada", responsavel=self.operador, prazo=self.hoje)
        resposta = self.pagina(self.operador)
        self.assertContains(resposta, "Conferir bomba")
        self.assertContains(resposta, "Trocar lâmpada")

    def test_mostra_demanda_que_apenas_acompanho(self):
        self._demanda(
            "Orçamento do elevador",
            responsavel_acompanhamento=self.operador,
            prazo_proxima_acao=self.hoje,
        )
        self.assertContains(self.pagina(self.operador), "Orçamento do elevador")

    def test_demanda_sem_prazo_vencido_fica_de_fora(self):
        self._demanda(
            "Melhoria futura",
            responsavel=self.operador,
            prazo=self.hoje + timedelta(days=10),
        )
        self.assertNotContains(self.pagina(self.operador), "Melhoria futura")


class IsolamentoDoCondominioTests(TestCase):
    """A tela nunca mostra item de condomínio fora do escopo do usuário."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.alpha, Papel.SINDICA)
        criar_vinculo(self.gestao, self.beta, Papel.SINDICA)
        self.operador = criar_pessoa("operador@exemplo.test", "Operador")
        criar_vinculo(self.operador, self.alpha, Papel.OPERACIONAL)
        criar_vinculo(self.operador, self.beta, Papel.OPERACIONAL)
        self.client.force_login(self.operador)

    def _manutencao_do_beta(self):
        return Manutencao.objects.create(
            condominio=self.beta,
            criado_por=self.gestao,
            titulo="Só do Beta",
            responsavel=self.operador,
            data_prevista=self.hoje,
        )

    def test_item_de_outro_condominio_nao_aparece(self):
        self._manutencao_do_beta()
        self.assertNotContains(
            self.client.get(reverse("core:meu_dia")), "Só do Beta"
        )

    def test_escolha_no_endereco_troca_o_condominio_em_vista(self):
        self._manutencao_do_beta()
        resposta = self.client.get(
            reverse("core:meu_dia"), {"condominio": self.beta.pk}
        )
        self.assertContains(resposta, "Só do Beta")


class OrdemDoMeuDiaTests(BaseDoMeuDiaTests):
    """Atrasados primeiro, do mais antigo ao mais recente; depois os de hoje."""

    def test_atrasado_vem_antes_do_de_hoje(self):
        self._manutencao(self.operador, "Serviço de hoje", data_prevista=self.hoje)
        self._manutencao(
            self.operador,
            "Serviço atrasado",
            data_prevista=self.hoje - timedelta(days=3),
        )
        texto = self.pagina(self.operador).content.decode()
        self.assertLess(
            texto.index("Serviço atrasado"), texto.index("Serviço de hoje")
        )

    def test_o_atraso_mais_antigo_vem_primeiro(self):
        self._manutencao(
            self.operador,
            "Atraso recente",
            data_prevista=self.hoje - timedelta(days=1),
        )
        self._manutencao(
            self.operador,
            "Atraso antigo",
            data_prevista=self.hoje - timedelta(days=9),
        )
        texto = self.pagina(self.operador).content.decode()
        self.assertLess(texto.index("Atraso antigo"), texto.index("Atraso recente"))

    def test_rotulo_de_atraso_conta_os_dias(self):
        self._manutencao(
            self.operador,
            "Serviço atrasado",
            data_prevista=self.hoje - timedelta(days=3),
        )
        self.assertContains(self.pagina(self.operador), "Atrasado 3 dias")

    def test_rotulo_de_hoje(self):
        self._manutencao(self.operador, "Serviço de hoje", data_prevista=self.hoje)
        self.assertContains(self.pagina(self.operador), "Hoje")


class VazioDoMeuDiaTests(BaseDoMeuDiaTests):
    """Sem nada para fazer, a tela diz isso com clareza."""

    def test_sem_itens_mostra_a_mensagem_de_vazio(self):
        self.assertContains(self.pagina(self.operador), "Tudo certo por hoje")

    def test_com_item_nao_mostra_a_mensagem_de_vazio(self):
        self._manutencao(self.operador, "Ajustar portão", data_prevista=self.hoje)
        self.assertNotContains(self.pagina(self.operador), "Tudo certo por hoje")


class MenuDoMeuDiaTests(BaseDoMeuDiaTests):
    """O item de menu existe só para quem conduz o dia."""

    def _home(self, usuario):
        self.client.force_login(usuario)
        return self.client.get(reverse("core:home"))

    def test_operacao_ve_o_item_de_menu(self):
        self.assertContains(self._home(self.operador), 'href="/meu-dia/"')

    def test_supervisao_ve_o_item_de_menu(self):
        supervisora = criar_pessoa("super@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, Papel.SUPERVISORA)
        self.assertContains(self._home(supervisora), 'href="/meu-dia/"')

    def test_portaria_ve_o_item_de_menu(self):
        porteiro = criar_pessoa("porteiro@exemplo.test", "Porteiro")
        criar_vinculo(porteiro, self.condominio, Papel.PORTEIRO)
        self.assertContains(self._home(porteiro), 'href="/meu-dia/"')

    def test_gestao_nao_ve_o_item_de_menu(self):
        self.assertNotContains(self._home(self.gestao), 'href="/meu-dia/"')
