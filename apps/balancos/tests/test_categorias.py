"""Mão de obra é Pessoal, nunca Obras — e o que o comando faz com os meses já confirmados.

Estes testes fixam a regra ("locação de mão de obra" não é obra) e o comando de
reclassificação: sem ``--aplicar`` nada muda; com ``--aplicar`` a categoria da
máquina muda, a decisão manual permanece e os totais oficiais ficam intactos.

Fixam também a classificação pela descrição antes do grupo, a regra aprendida
com a correção, o sistema de manutenção pelo fornecedor e a tela das despesas a
classificar: a sugestão da IA não grava nada sozinha.
"""
import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.balancos import classificacao, correcoes
from apps.balancos.categorias import (
    CategoriaEithra,
    SistemaManutencao,
    categoria_para,
    categorizar,
    sistema_para,
)
from apps.balancos.management.commands.reclassificar_balancos import (
    reclassificar_leitura,
)
from apps.balancos.models import RegraClassificacao
from apps.balancos.tests.apoio import criar_vinculo
from apps.balancos.tests.test_models import criar_balanco, criar_pessoa
from apps.cadastros.models import Fornecedor
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio
from apps.governanca.models import ContratoFornecedor

AGOSTO = datetime.date(2026, 8, 1)


class MaoDeObraTests(TestCase):
    """A regra de categorização em si."""

    def test_locacao_de_mao_de_obra_e_pessoal(self):
        self.assertEqual(categorizar("Locação de Mão de Obra"), CategoriaEithra.PESSOAL)

    def test_obra_da_fachada_continua_obras(self):
        self.assertEqual(
            categorizar("Obra da fachada"), CategoriaEithra.OBRAS_MELHORIAS
        )

    def test_terceirizacao_e_pessoal(self):
        self.assertEqual(
            categorizar("Terceirização de serviços"), CategoriaEithra.PESSOAL
        )


def _despesa(descricao, valor, categoria, *, categoria_original=None, correcao=None):
    """Um lançamento lido, no formato que vive dentro da leitura do balanço."""
    item = {
        "id": "".join(descricao.split())[:12].lower(),
        "descricao_original": descricao,
        "descricao_normalizada": descricao.lower(),
        "valor": valor,
        "data": None,
        "fornecedor_candidato": None,
        "documento": None,
        "parcela": None,
        "categoria_original": categoria_original,
        "categoria_eithra": categoria,
        "natureza": "recorrente",
        "confianca": "media",
    }
    if correcao:
        item["correcao"] = correcao
    return item


def _validado(*despesas):
    """Um ``dados_validados`` mínimo, válido pelo contrato, com as despesas dadas."""
    leitura = {
        "schema_versao": 1,
        "competencia": AGOSTO.isoformat(),
        "receitas_total": "1000.00",
        "despesas_total": "600.00",
        "soma_despesas_extraida": "600.00",
        "saldos": [],
        "categorias": [],
        "grupos": [],
        "receitas": [],
        "despesas": list(despesas),
        "provenance": {},
        "resumo": {},
    }
    return {
        "schema_versao": 1,
        "origem_schema_versao": 1,
        "competencia": AGOSTO.isoformat(),
        "receitas_total": "1000.00",
        "despesas_total": "600.00",
        "total_declarado_despesas": "600.00",
        "total_detalhado_identificado": "600.00",
        "valor_nao_associado": "0.00",
        "percentual_detalhamento": "100.00",
        "confirmado_com_ressalvas": False,
        "confirmado_em": None,
        "leitura": leitura,
    }


class ReclassificarLeituraTests(TestCase):
    """A releitura de uma leitura, sem banco."""

    def test_rele_so_o_que_a_maquina_classificou(self):
        leitura = _validado(
            _despesa(
                "Locação de Mão de Obra", "300.00", CategoriaEithra.OBRAS_MELHORIAS
            ),
            _despesa(
                "Serviço avulso",
                "300.00",
                CategoriaEithra.OBRAS_MELHORIAS,
                correcao={"categoria_eithra": CategoriaEithra.MANUTENCAO},
            ),
        )["leitura"]

        mudancas = reclassificar_leitura(leitura)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0]["para"], CategoriaEithra.PESSOAL)
        self.assertEqual(
            leitura["despesas"][0]["categoria_eithra"], CategoriaEithra.PESSOAL
        )
        # A corrigida à mão não muda e nem aparece na lista de mudanças.
        self.assertEqual(
            leitura["despesas"][1]["categoria_eithra"], CategoriaEithra.OBRAS_MELHORIAS
        )


class ComandoReclassificarTests(TestCase):
    """O comando sobre o balanço já confirmado."""

    def setUp(self):
        self.gestao = criar_pessoa()
        self.condominio = Condominio.objects.create(nome="Condomínio A")

    def _balanco(self, *despesas):
        return criar_balanco(
            self.condominio,
            self.gestao,
            competencia=AGOSTO,
            dados_validados=_validado(*despesas),
        )

    def test_sem_aplicar_nao_grava(self):
        balanco = self._balanco(
            _despesa(
                "Locação de Mão de Obra", "300.00", CategoriaEithra.OBRAS_MELHORIAS
            )
        )

        saida = StringIO()
        call_command("reclassificar_balancos", stdout=saida)

        balanco.refresh_from_db()
        despesas = balanco.dados_validados["leitura"]["despesas"]
        self.assertEqual(
            despesas[0]["categoria_eithra"], CategoriaEithra.OBRAS_MELHORIAS
        )
        self.assertIn("Pessoal", saida.getvalue())
        self.assertIn("nada foi", saida.getvalue())

    def test_aplicar_reclassifica_preserva_manual_e_totais(self):
        balanco = self._balanco(
            _despesa(
                "Locação de Mão de Obra", "300.00", CategoriaEithra.OBRAS_MELHORIAS
            ),
            _despesa(
                "Serviço avulso",
                "300.00",
                CategoriaEithra.OBRAS_MELHORIAS,
                correcao={"categoria_eithra": CategoriaEithra.MANUTENCAO},
            ),
        )

        call_command("reclassificar_balancos", "--aplicar")

        balanco.refresh_from_db()
        dados = balanco.dados_validados
        despesas = dados["leitura"]["despesas"]

        # A que a máquina errou passa a Pessoal.
        self.assertEqual(despesas[0]["categoria_eithra"], CategoriaEithra.PESSOAL)
        # A corrigida à mão continua como a gestão decidiu.
        self.assertEqual(
            despesas[1]["categoria_eithra"], CategoriaEithra.OBRAS_MELHORIAS
        )
        self.assertEqual(
            despesas[1]["correcao"]["categoria_eithra"], CategoriaEithra.MANUTENCAO
        )
        # Totais oficiais intactos.
        self.assertEqual(dados["receitas_total"], "1000.00")
        self.assertEqual(dados["despesas_total"], "600.00")
        # Rastreável no histórico do balanço.
        revisoes = dados["leitura"]["revisoes"]
        self.assertEqual(revisoes[-1]["tipo"], "reclassificacao")
        self.assertIn("Reclassificação automática", revisoes[-1]["justificativa"])


class ClassificacaoPelaDescricaoTests(TestCase):
    """A descrição manda; o grupo é a última pista — o conserto do "telefone"."""

    def test_telefone_no_grupo_de_agua_vira_tecnologia(self):
        self.assertEqual(
            categorizar("Telefone", categoria_original="Água, luz e telefone"),
            CategoriaEithra.TECNOLOGIA,
        )

    def test_descricao_sem_palavra_usa_o_grupo(self):
        self.assertEqual(
            categorizar("Rateio do mês", categoria_original="Energia"),
            CategoriaEithra.ENERGIA,
        )


class FornecedorComContratoTests(TestCase):
    """O fornecedor com contrato ativo decide a categoria antes das palavras."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio A")

    def test_categoria_vem_do_contrato_do_fornecedor(self):
        fornecedor = Fornecedor.objects.create(
            condominio=self.condominio, nome_razao_social="Atlas Elevadores Ltda"
        )
        ContratoFornecedor.objects.create(
            condominio=self.condominio,
            fornecedor=fornecedor,
            objeto="Manutenção preventiva de elevadores",
            data_inicio=datetime.date(2026, 1, 1),
            ativo=True,
        )

        categoria = categoria_para(
            self.condominio,
            descricao="Serviço mensal",
            fornecedor="Atlas Elevadores",
        )

        self.assertEqual(categoria, CategoriaEithra.ELEVADORES)


class SistemaDeManutencaoTests(TestCase):
    """O sistema separa a manutenção por equipamento, sem mexer no valor."""

    def test_bsa_e_bombas_e_agua(self):
        self.assertEqual(
            sistema_para("Manutenção preventiva", fornecedor="BSA"),
            SistemaManutencao.BOMBAS_E_AGUA,
        )

    def test_ps_eletro_e_gerador(self):
        self.assertEqual(
            sistema_para("Serviço mensal", fornecedor="PS Eletro"),
            SistemaManutencao.GERADOR,
        )


class RegraAprendidaTests(TestCase):
    """A correção vira regra e o mês seguinte já nasce classificado."""

    def setUp(self):
        self.gestao = criar_pessoa()
        self.condominio = Condominio.objects.create(nome="Condomínio A")

    def test_correcao_cria_regra_e_proximo_mes_usa(self):
        balanco = criar_balanco(
            self.condominio,
            self.gestao,
            competencia=AGOSTO,
            dados_validados=_validado(
                _despesa(
                    "Serviço recorrente 08/2026",
                    "300.00",
                    CategoriaEithra.NAO_CLASSIFICADO,
                )
            ),
        )
        leitura = balanco.dados_validados["leitura"]
        correcoes.aplicar_correcao(
            leitura,
            lancamento_id=leitura["despesas"][0]["id"],
            campos={"categoria_eithra": CategoriaEithra.MANUTENCAO},
            justificativa="É manutenção do mês.",
            usuario=self.gestao,
            condominio=self.condominio,
        )

        regra = RegraClassificacao.objects.get(condominio=self.condominio)
        self.assertEqual(regra.categoria, CategoriaEithra.MANUTENCAO)

        # O mês seguinte, com a mesma despesa, nasce pela regra.
        proximo = _validado(
            _despesa(
                "Serviço recorrente 09/2026",
                "310.00",
                CategoriaEithra.NAO_CLASSIFICADO,
            )
        )["leitura"]
        reclassificar_leitura(proximo, condominio=self.condominio)

        self.assertEqual(
            proximo["despesas"][0]["categoria_eithra"], CategoriaEithra.MANUTENCAO
        )


class TelaClassificarTests(TestCase):
    """A tela das despesas a classificar: sugestão não grava; confirmar, sim."""

    def setUp(self):
        self.gestao = criar_pessoa(email="sindica@exemplo.com.br", nome="Síndica")
        self.condominio = Condominio.objects.create(nome="Condomínio A")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.balanco = criar_balanco(
            self.condominio,
            self.gestao,
            competencia=AGOSTO,
            dados_validados=_validado(
                _despesa(
                    "Despesa sem pista", "200.00", CategoriaEithra.NAO_CLASSIFICADO
                )
            ),
        )
        self.client.force_login(self.gestao)

    def _leitura(self):
        return self.balanco.dados_validados["leitura"]

    def test_sugestao_da_ia_nao_grava_sem_confirmacao(self):
        with mock.patch(
            "apps.core.llm.pedir_json",
            return_value={"categoria": CategoriaEithra.MANUTENCAO},
        ):
            linhas = classificacao.linhas_a_classificar(
                self._leitura(), condominio=self.condominio
            )

        self.assertEqual(linhas[0]["sugestao"], CategoriaEithra.MANUTENCAO)
        self.assertEqual(linhas[0]["sugestao_origem"], classificacao.ORIGEM_IA)
        # A sugestão não gravou nada: nem regra, nem categoria no lançamento.
        self.assertFalse(RegraClassificacao.objects.exists())
        self.balanco.refresh_from_db()
        self.assertEqual(
            self._leitura()["despesas"][0]["categoria_eithra"],
            CategoriaEithra.NAO_CLASSIFICADO,
        )

    def test_confirmar_classifica_cria_regra_e_preserva_totais(self):
        resposta = self.client.post(
            reverse("balancos:classificar_confirmar", args=[self.balanco.pk]),
            {
                "lancamento_id": self._leitura()["despesas"][0]["id"],
                "categoria": CategoriaEithra.MANUTENCAO,
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.balanco.refresh_from_db()
        dados = self.balanco.dados_validados
        efetivas = correcoes.despesas_efetivas(dados["leitura"])
        self.assertEqual(efetivas[0]["categoria_eithra"], CategoriaEithra.MANUTENCAO)
        # O que foi lido não é reescrito: a decisão vive na correção.
        self.assertEqual(
            efetivas[0]["lido"]["categoria_eithra"], CategoriaEithra.NAO_CLASSIFICADO
        )
        self.assertTrue(
            RegraClassificacao.objects.filter(
                condominio=self.condominio, categoria=CategoriaEithra.MANUTENCAO
            ).exists()
        )
        # Totais oficiais intactos.
        self.assertEqual(dados["receitas_total"], "1000.00")
        self.assertEqual(dados["despesas_total"], "600.00")
