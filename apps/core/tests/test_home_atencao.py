"""A seção operacional da Home: o que precisa de atenção agora.

A Home não calcula atenção — ela consome a leitura derivada das manutenções e
apresenta. Estes testes travam isso: o agrupamento entre "Hoje" e "Próximos
dias", a contagem, a precedência, o isolamento por condomínio, o clique para o
detalhe, a ausência de identificadores técnicos, a forma de lista (não tabela) e
a fonte única das regras.
"""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.manutencoes.constants import (
    StatusConferencia,
    StatusManutencao,
    TipoManutencao,
)
from apps.manutencoes.models import Manutencao
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)


def _manutencao(condominio, criado_por, **campos):
    """Uma manutenção do condomínio, com o que o teste quiser trocar."""
    base = {
        "titulo": "Serviço de teste",
        "status": StatusManutencao.ABERTA,
        "tipo": TipoManutencao.CORRETIVA,
    }
    base.update(campos)
    return Manutencao.objects.create(
        condominio=condominio, criado_por=criado_por, **base
    )


class BaseDaHomeComAtencaoTests(TestCase):
    """Um condomínio, a gestão que entra e um responsável de operação."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.responsavel = criar_pessoa("responsavel@exemplo.test", "Responsável")
        criar_vinculo(self.responsavel, self.condominio, Papel.OPERACIONAL)
        self.client.force_login(self.gestao)

    def home(self):
        return self.client.get(reverse("core:home"))

    def texto_da_home(self) -> str:
        return self.home().content.decode()


class BlocoDeAtencaoTests(BaseDaHomeComAtencaoTests):
    """O bloco "Hoje" existe, conta, ordena e leva ao detalhe."""

    def test_mostra_o_bloco_quando_ha_atencao(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Bomba do bloco A parou",
            data_prevista=self.hoje - timedelta(days=2),
            responsavel=self.responsavel,
        )
        resposta = self.home()
        self.assertContains(resposta, "Hoje")
        self.assertContains(resposta, "Bomba do bloco A parou")

    def test_conta_o_que_precisa_de_atencao(self):
        for indice in range(2):
            _manutencao(
                self.condominio,
                self.gestao,
                titulo=f"Serviço {indice}",
                data_prevista=self.hoje - timedelta(days=indice + 1),
                responsavel=self.responsavel,
            )
        self.assertContains(self.home(), "2 coisas precisam da sua atenção")

    def test_uma_atencao_fala_no_singular(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        self.assertContains(self.home(), "1 coisa precisa da sua atenção")

    def test_itens_seguem_a_precedencia(self):
        _manutencao(  # hoje — precedência 4, o menos urgente dos três
            self.condominio,
            self.gestao,
            titulo="Item de hoje",
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        _manutencao(  # atrasada — precedência 3
            self.condominio,
            self.gestao,
            titulo="Item atrasado",
            data_prevista=self.hoje - timedelta(days=3),
            responsavel=self.responsavel,
        )
        _manutencao(  # aguardando correção — precedência 0, o mais urgente
            self.condominio,
            self.gestao,
            titulo="Item reprovado",
            data_prevista=self.hoje - timedelta(days=1),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.REPROVADA,
            data_execucao=timezone.now(),
        )
        texto = self.texto_da_home()
        self.assertLess(texto.index("Item reprovado"), texto.index("Item atrasado"))
        self.assertLess(texto.index("Item atrasado"), texto.index("Item de hoje"))

    def test_concluidas_nao_aparecem(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Serviço encerrado",
            status=StatusManutencao.CONCLUIDA,
            data_prevista=self.hoje - timedelta(days=5),
            data_conclusao=timezone.now(),
            responsavel=self.responsavel,
        )
        self.assertNotContains(self.home(), "Serviço encerrado")

    def test_sem_atencao_mostra_estado_simples(self):
        self.assertContains(self.home(), "Tudo em ordem por aqui.")

    def test_item_aponta_para_o_detalhe(self):
        manutencao = _manutencao(
            self.condominio,
            self.gestao,
            titulo="Troca de lâmpada",
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        self.assertContains(
            self.home(), reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

    def test_nao_expoe_termos_tecnicos(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Troca de lâmpada",
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        texto = self.texto_da_home()
        for termo in (
            "aguardando_correcao",
            "aguardando_conferencia",
            "aguardando_conclusao",
            "sem_responsavel",
            "status_conferencia",
            "competencia_preventiva",
        ):
            self.assertNotIn(termo, texto)


class ProximosDiasTests(BaseDaHomeComAtencaoTests):
    """O que está por vir fica separado do que exige ação agora."""

    def test_proxima_nao_conta_como_hoje(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Poda programada",
            data_prevista=self.hoje + timedelta(days=2),
            responsavel=self.responsavel,
        )
        resposta = self.home()
        self.assertContains(resposta, "Próximos dias")
        self.assertContains(resposta, "Poda programada")
        self.assertNotContains(resposta, "coisas precisam da sua atenção")
        self.assertContains(resposta, "Tudo em ordem por aqui.")

    def test_hoje_e_proximos_aparecem_separados(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Atrasado de ontem",
            data_prevista=self.hoje - timedelta(days=1),
            responsavel=self.responsavel,
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Por vir",
            data_prevista=self.hoje + timedelta(days=2),
            responsavel=self.responsavel,
        )
        texto = self.texto_da_home()
        self.assertIn("Atrasado de ontem", texto)
        self.assertIn("Por vir", texto)
        self.assertLess(
            texto.index("Atrasado de ontem"), texto.index("Próximos dias")
        )
        self.assertLess(texto.index("Próximos dias"), texto.index("Por vir"))
        self.assertContains(self.home(), "1 coisa precisa da sua atenção")


class LimiteDaHomeTests(BaseDaHomeComAtencaoTests):
    """A Home mostra um punhado e oferece "Ver todas" — nunca uma lista enorme."""

    def test_mostra_no_maximo_cinco_itens(self):
        for indice in range(7):
            _manutencao(
                self.condominio,
                self.gestao,
                titulo=f"Serviço {indice}",
                data_prevista=self.hoje - timedelta(days=indice + 1),
                responsavel=self.responsavel,
            )
        resposta = self.home()
        self.assertContains(resposta, "7 coisas precisam da sua atenção")
        self.assertContains(resposta, "Ver todas")
        texto = resposta.content.decode()
        self.assertEqual(texto.count('<li class="eithra-linha">'), 5)

    def test_sem_excedente_nao_oferece_ver_todas(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        self.assertNotContains(self.home(), "Ver todas")


class IsolamentoDaHomeTests(TestCase):
    """A Home nunca recebe atenção de condomínio fora do escopo do usuário."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.alpha, Papel.SINDICA)
        self.client.force_login(self.gestao)

    def test_nao_mostra_manutencao_de_condominio_sem_acesso(self):
        _manutencao(
            self.beta,
            self.gestao,
            titulo="Segredo de outro condomínio",
            data_prevista=self.hoje - timedelta(days=2),
        )
        resposta = self.client.get(reverse("core:home"))
        self.assertNotContains(resposta, "Segredo de outro condomínio")
        self.assertContains(resposta, "Tudo em ordem por aqui.")


class ResponsividadeDaHomeTests(BaseDaHomeComAtencaoTests):
    """A lista de atenção é lista, não tabela, e o item inteiro é clicável."""

    def test_a_atencao_nao_usa_tabela(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Item de hoje",
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        texto = self.texto_da_home()
        self.assertIn('class="eithra-lista', texto)
        self.assertNotIn("<table", texto)

    def test_o_item_inteiro_e_clicavel(self):
        manutencao = _manutencao(
            self.condominio,
            self.gestao,
            titulo="Item de hoje",
            data_prevista=self.hoje,
            responsavel=self.responsavel,
        )
        texto = self.texto_da_home()
        endereco = reverse("manutencoes:detalhe", args=[manutencao.pk])
        self.assertIn(f'href="{endereco}"', texto)
        self.assertIn("eithra-linha-corpo", texto)


class PerformanceDaHomeTests(BaseDaHomeComAtencaoTests):
    """A Home não faz uma consulta por item da lista."""

    def _consultas(self) -> int:
        with CaptureQueriesContext(connection) as capturadas:
            self.client.get(reverse("core:home"))
        return len(capturadas)

    def _uma_atencao(self, indice):
        local = criar_local(self.condominio, nome=f"Local {indice}")
        fornecedor = criar_fornecedor(self.condominio, nome=f"Fornecedor {indice}")
        _manutencao(
            self.condominio,
            self.gestao,
            titulo=f"Serviço {indice}",
            data_prevista=self.hoje - timedelta(days=indice + 1),
            responsavel=self.responsavel,
            local=local,
            fornecedor=fornecedor,
        )

    def test_consultas_nao_crescem_com_o_numero_de_itens(self):
        self._uma_atencao(0)
        com_um = self._consultas()
        for indice in range(1, 5):
            self._uma_atencao(indice)
        com_cinco = self._consultas()
        self.assertEqual(com_um, com_cinco)


class FonteUnicaDaHomeTests(SimpleTestCase):
    """A Home consome a leitura do domínio; não reescreve as regras de atenção."""

    def _texto(self, relativo) -> str:
        return (Path(settings.BASE_DIR) / relativo).read_text(encoding="utf-8")

    def test_a_view_usa_o_servico_de_atencao(self):
        texto = self._texto("apps/core/views.py")
        self.assertIn("obter_atencoes_de_manutencoes", texto)

    def test_nem_a_view_nem_o_template_reescrevem_as_frases(self):
        frases = [
            "Atrasada há",
            "execução não registrada",
            "Vence hoje",
            "Correção pendente",
            "Aguardando conferência",
            "falta concluir a manutenção",
            "Prevista para",
        ]
        for relativo in ("apps/core/views.py", "templates/pages/home.html"):
            texto = self._texto(relativo)
            for frase in frases:
                self.assertNotIn(frase, texto)
