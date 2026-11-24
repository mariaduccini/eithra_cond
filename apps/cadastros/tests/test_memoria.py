"""A memória de um ativo e de um lugar: o que a tela mostra e de onde lê.

Estes testes prendem a regra central das telas de memória: nada é copiado para o
equipamento nem para o local. O que aparece vem do vínculo que cada domínio já
tem — a demanda aponta para o ativo, a manutenção aponta para o local, a
Governança responde por contrato e garantia. A reincidência é conservadora: só
conta repetição com a mesma natureza estrutural, nunca dois títulos parecidos.

Nenhum dado real entra aqui. As demandas, manutenções e provas são de teste, e a
Governança é lida pela permissão de quem pergunta, como no sistema.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.memoria import (
    JANELA_DA_REINCIDENCIA_DIAS,
    memoria_do_equipamento,
    memoria_do_local,
)
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
    criar_vinculo,
    entrar_como,
)
from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda, TipoProblema
from apps.demandas.models import Demanda
from apps.governanca.models import (
    ContratoEquipamento,
    ContratoLocal,
    DocumentoEquipamento,
    GarantiaEquipamento,
)
from apps.governanca.tests.apoio import (
    criar_contrato,
    criar_documento,
    criar_garantia,
    criar_manutencao,
)
from apps.manutencoes.constants import (
    ContextoEvidencia,
    StatusManutencao,
    TipoEvidenciaManutencao,
)
from apps.manutencoes.models import EvidenciaManutencao, PlanoPreventivo
from apps.manutencoes.tests.apoio import criar_demanda


def _adicionar_evidencia(manutencao, usuario, tipo=TipoEvidenciaManutencao.FOTO):
    """Uma prova presa à manutenção, criada direto para a memória poder lê-la.

    O portal de escrita da evidência continua sendo o serviço do domínio; aqui só
    se precisa do registro já existente para a memória projetá-lo.
    """
    return EvidenciaManutencao.objects.create(
        manutencao=manutencao,
        contexto=ContextoEvidencia.EXECUCAO,
        tipo_midia=tipo,
        storage_key="condominios/a/manutencoes/b/evidencias/c.jpg",
        mime_type="image/jpeg",
        sha256="a" * 64,
        enviada_por=usuario,
    )


class MemoriaDoEquipamentoTests(TestCase):
    """O que já aconteceu com o ativo, lido dos vínculos que existem."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)
        self.local = criar_local(self.condominio, nome="Casa de máquinas")
        self.ativo = criar_equipamento(
            self.condominio,
            nome="Bomba de recalque",
            local=self.local,
            fabricante="Weg",
            modelo="W-22",
        )

    def test_identidade_traz_o_ativo_e_aponta_para_o_local(self):
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["ativo"]["pk"], self.ativo.pk)
        self.assertEqual(memoria["ativo"]["nome"], "Bomba de recalque")
        self.assertTrue(memoria["ativo"]["ativo"])
        self.assertEqual(memoria["local"]["nome"], "Casa de máquinas")
        self.assertEqual(
            memoria["local"]["url"],
            reverse("cadastros:local", args=[self.local.pk]),
        )

    def test_campo_sem_resposta_nao_vira_par_vazio(self):
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        rotulos = [par["rotulo"] for par in memoria["ativo"]["pares"]]
        self.assertIn("Fabricante", rotulos)
        self.assertNotIn("Número de série", rotulos)
        self.assertNotIn("", [par["valor"] for par in memoria["ativo"]["pares"]])

    def test_abertos_reune_demanda_e_manutencao_em_aberto(self):
        demanda = criar_demanda(
            self.condominio,
            self.sindica,
            titulo="Vazamento na casa de máquinas",
            equipamento=self.ativo,
            status=StatusDemanda.ABERTA,
        )
        manutencao = criar_manutencao(
            self.condominio,
            self.sindica,
            equipamento=self.ativo,
            status=StatusManutencao.ABERTA,
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        titulos = [item["titulo"] for item in memoria["abertos"]]
        self.assertIn(demanda.titulo, titulos)
        self.assertIn(manutencao.titulo, titulos)

    def test_demanda_encerrada_nao_conta_como_aberta(self):
        criar_demanda(
            self.condominio,
            self.sindica,
            titulo="Antiga",
            equipamento=self.ativo,
            status=StatusDemanda.CONCLUIDA,
            concluido_em=timezone.now(),
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["abertos"], [])

    def test_manutencao_mostra_o_resultado_que_deixou(self):
        criar_manutencao(
            self.condominio,
            self.sindica,
            equipamento=self.ativo,
            status=StatusManutencao.CONCLUIDA,
            data_conclusao=timezone.now(),
            descricao_servico="Trocado o selo mecânico.",
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        resultados = [linha["resultado"] for linha in memoria["manutencoes"]]
        self.assertIn("Trocado o selo mecânico.", resultados)

    def test_proxima_manutencao_vem_do_plano_preventivo(self):
        PlanoPreventivo.objects.create(
            condominio=self.condominio,
            titulo="Revisão trimestral",
            equipamento=self.ativo,
            proxima_data_prevista=timezone.localdate() + timedelta(days=10),
            criado_por=self.sindica,
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["proxima"]["titulo"], "Revisão trimestral")
        self.assertIn("10 dias", memoria["proxima"]["detalhe"])

    def test_sem_plano_ativo_nao_ha_proxima_manutencao(self):
        PlanoPreventivo.objects.create(
            condominio=self.condominio,
            titulo="Plano antigo",
            equipamento=self.ativo,
            proxima_data_prevista=timezone.localdate(),
            ativo=False,
            criado_por=self.sindica,
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertIsNone(memoria["proxima"])

    def test_fornecedores_declarados_pela_origem(self):
        do_contrato = criar_fornecedor(
            self.condominio, nome_razao_social="Elevadores Silva"
        )
        contrato = criar_contrato(
            self.condominio, self.sindica, fornecedor=do_contrato
        )
        ContratoEquipamento.objects.create(contrato=contrato, equipamento=self.ativo)
        da_manutencao = criar_fornecedor(
            self.condominio, nome_razao_social="Hidráulica Bahia"
        )
        criar_manutencao(
            self.condominio,
            self.sindica,
            equipamento=self.ativo,
            fornecedor=da_manutencao,
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        por_nome = {linha["nome"]: linha["via"] for linha in memoria["fornecedores"]}
        self.assertEqual(por_nome[str(do_contrato)], "Contrato")
        self.assertEqual(por_nome[str(da_manutencao)], "Manutenção")

    def test_governanca_vazia_para_quem_nao_pode_consultar(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        memoria = memoria_do_equipamento(self.ativo, morador)
        self.assertFalse(memoria["governanca"]["pode_consultar"])
        self.assertEqual(memoria["governanca"]["contratos"], [])
        self.assertEqual(memoria["governanca"]["garantias"], [])

    def test_contrato_e_garantia_que_cobrem_o_ativo(self):
        contrato = criar_contrato(
            self.condominio, self.sindica, objeto="Manutenção de bombas"
        )
        ContratoEquipamento.objects.create(contrato=contrato, equipamento=self.ativo)
        garantia = criar_garantia(
            self.condominio, self.sindica, descricao="Garantia do selo"
        )
        GarantiaEquipamento.objects.create(garantia=garantia, equipamento=self.ativo)
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertTrue(memoria["governanca"]["pode_consultar"])
        self.assertIn(
            "Manutenção de bombas",
            [linha["objeto"] for linha in memoria["governanca"]["contratos"]],
        )
        self.assertIn(
            "Garantia do selo",
            [linha["descricao"] for linha in memoria["governanca"]["garantias"]],
        )

    def test_documentos_do_equipamento(self):
        documento = criar_documento(
            self.condominio, self.sindica, titulo="Manual da bomba"
        )
        DocumentoEquipamento.objects.create(documento=documento, equipamento=self.ativo)
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertIn(
            "Manual da bomba", [linha["titulo"] for linha in memoria["documentos"]]
        )

    def test_reincidencia_conta_o_mesmo_tema_no_mesmo_ativo(self):
        for _ in range(3):
            criar_demanda(
                self.condominio,
                self.sindica,
                equipamento=self.ativo,
                tipo_problema=TipoProblema.VAZAMENTO,
            )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(len(memoria["reincidencia"]), 1)
        self.assertEqual(memoria["reincidencia"][0]["tema"], "Vazamento")
        self.assertEqual(memoria["reincidencia"][0]["quantidade"], 3)

    def test_uma_ocorrencia_so_nao_e_reincidencia(self):
        criar_demanda(
            self.condominio,
            self.sindica,
            equipamento=self.ativo,
            tipo_problema=TipoProblema.VAZAMENTO,
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["reincidencia"], [])

    def test_titulos_parecidos_sem_classificacao_nao_contam(self):
        for _ in range(3):
            criar_demanda(
                self.condominio,
                self.sindica,
                equipamento=self.ativo,
                titulo="Problema na bomba",
            )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["reincidencia"], [])

    def test_ocorrencia_fora_da_janela_nao_conta(self):
        antiga = criar_demanda(
            self.condominio,
            self.sindica,
            equipamento=self.ativo,
            tipo_problema=TipoProblema.VAZAMENTO,
        )
        Demanda.objects.filter(pk=antiga.pk).update(
            criado_em=timezone.now() - timedelta(days=JANELA_DA_REINCIDENCIA_DIAS + 1)
        )
        criar_demanda(
            self.condominio,
            self.sindica,
            equipamento=self.ativo,
            tipo_problema=TipoProblema.VAZAMENTO,
        )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["reincidencia"], [])

    def test_demanda_de_outro_ativo_nao_entra_na_reincidencia(self):
        outro = criar_equipamento(self.condominio, nome="Portão")
        for _ in range(3):
            criar_demanda(
                self.condominio,
                self.sindica,
                equipamento=outro,
                tipo_problema=TipoProblema.VAZAMENTO,
            )
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(memoria["reincidencia"], [])
        self.assertEqual(memoria["demandas"], [])

    def test_ultima_evidencia_aponta_para_a_manutencao(self):
        manutencao = criar_manutencao(
            self.condominio, self.sindica, equipamento=self.ativo
        )
        _adicionar_evidencia(manutencao, self.sindica)
        memoria = memoria_do_equipamento(self.ativo, self.sindica)
        self.assertEqual(len(memoria["evidencias"]), 1)
        self.assertEqual(memoria["evidencias"][0]["tipo"], "Foto")
        self.assertEqual(
            memoria["evidencias"][0]["url"],
            reverse("manutencoes:detalhe", args=[manutencao.pk]),
        )


class MemoriaDoLocalTests(TestCase):
    """O que já aconteceu neste lugar, projetado dos vínculos do local."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)
        self.local = criar_local(self.condominio, nome="Garagem")

    def test_ativos_do_local_com_contagem_e_endereco(self):
        ativo = criar_equipamento(self.condominio, nome="Portão", local=self.local)
        memoria = memoria_do_local(self.local, self.sindica)
        self.assertEqual(memoria["total_de_ativos"], 1)
        self.assertEqual(memoria["ativos"][0]["nome"], "Portão")
        self.assertEqual(
            memoria["ativos"][0]["url"],
            reverse("cadastros:equipamento", args=[ativo.pk]),
        )

    def test_ativo_de_outro_local_nao_entra(self):
        outro = criar_local(self.condominio, nome="Piscina")
        criar_equipamento(self.condominio, nome="Bomba da piscina", local=outro)
        memoria = memoria_do_local(self.local, self.sindica)
        self.assertEqual(memoria["total_de_ativos"], 0)
        self.assertEqual(memoria["ativos"], [])

    def test_problemas_abertos_reune_demanda_e_manutencao(self):
        criar_demanda(
            self.condominio,
            self.sindica,
            local=self.local,
            titulo="Vazamento na garagem",
        )
        criar_manutencao(self.condominio, self.sindica, local=self.local)
        memoria = memoria_do_local(self.local, self.sindica)
        titulos = [item["titulo"] for item in memoria["abertos"]]
        self.assertIn("Vazamento na garagem", titulos)

    def test_reincidencia_no_local_conta_o_mesmo_tema(self):
        for _ in range(4):
            criar_demanda(
                self.condominio,
                self.sindica,
                local=self.local,
                tipo_problema=TipoProblema.INFILTRACAO,
            )
        memoria = memoria_do_local(self.local, self.sindica)
        self.assertEqual(memoria["reincidencia"][0]["tema"], "Infiltração")
        self.assertEqual(memoria["reincidencia"][0]["quantidade"], 4)

    def test_contrato_do_local_aparece_na_governanca(self):
        contrato = criar_contrato(
            self.condominio, self.sindica, objeto="Limpeza das áreas comuns"
        )
        ContratoLocal.objects.create(contrato=contrato, local=self.local)
        memoria = memoria_do_local(self.local, self.sindica)
        self.assertIn(
            "Limpeza das áreas comuns",
            [linha["objeto"] for linha in memoria["governanca"]["contratos"]],
        )

    def test_ultima_evidencia_da_manutencao_do_local(self):
        manutencao = criar_manutencao(self.condominio, self.sindica, local=self.local)
        _adicionar_evidencia(
            manutencao, self.sindica, tipo=TipoEvidenciaManutencao.DOCUMENTO
        )
        memoria = memoria_do_local(self.local, self.sindica)
        self.assertEqual(len(memoria["evidencias"]), 1)
        self.assertEqual(memoria["evidencias"][0]["tipo"], "Documento")

    def test_identidade_do_local_traz_nome_e_tipo(self):
        memoria = memoria_do_local(self.local, self.sindica)
        self.assertEqual(memoria["local"]["nome"], "Garagem")
        self.assertEqual(memoria["local"]["pk"], self.local.pk)
        self.assertTrue(memoria["local"]["ativo"])


class TelasDeMemoriaTests(TestCase):
    """As telas de detalhe: quem entra, o que vê e para onde apontam."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.local = criar_local(self.condominio, nome="Casa de máquinas")
        self.ativo = criar_equipamento(
            self.condominio, nome="Bomba de recalque", local=self.local
        )
        self.url_local = reverse("cadastros:local", args=[self.local.pk])
        self.url_ativo = reverse("cadastros:equipamento", args=[self.ativo.pk])

    def test_exige_login(self):
        self.assertEqual(self.client.get(self.url_local).status_code, 302)
        self.assertEqual(self.client.get(self.url_ativo).status_code, 302)

    def test_morador_recebe_404(self):
        entrar_como(self.client, Papel.MORADOR, self.condominio, "morador@exemplo.test")
        self.assertEqual(self.client.get(self.url_local).status_code, 404)
        self.assertEqual(self.client.get(self.url_ativo).status_code, 404)

    def test_registro_de_outro_condominio_e_404(self):
        vizinho = criar_condominio("Beta")
        local_alheio = criar_local(vizinho, nome="Casa de máquinas do vizinho")
        ativo_alheio = criar_equipamento(vizinho, nome="Bomba do vizinho")
        entrar_como(self.client, Papel.SINDICA, self.condominio, "sindica@exemplo.test")
        self.assertEqual(
            self.client.get(reverse("cadastros:local", args=[local_alheio.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("cadastros:equipamento", args=[ativo_alheio.pk])
            ).status_code,
            404,
        )

    def test_gestao_abre_a_memoria_do_ativo(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(self.url_ativo)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["memoria"]["ativo"]["pk"], self.ativo.pk)
        self.assertTrue(resposta.context["pode_gerenciar"])
        self.assertContains(resposta, "O que está aberto")
        self.assertContains(resposta, "Bomba de recalque")

    def test_gestao_abre_a_memoria_do_local(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(self.url_local)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["memoria"]["local"]["pk"], self.local.pk)
        self.assertContains(resposta, "Problemas abertos")
        self.assertContains(resposta, "Casa de máquinas")
        self.assertContains(resposta, "Ativos neste local")

    def test_link_de_edicao_aponta_para_o_registro_certo(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(self.url_ativo)
        self.assertContains(resposta, f"abrir=editar&item={self.ativo.pk}")

    def test_supervisora_le_sem_botao_de_editar(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        resposta = self.client.get(self.url_ativo)
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["pode_gerenciar"])
        self.assertNotContains(resposta, "Editar cadastro")

    def test_lista_de_locais_liga_para_a_memoria(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(reverse("cadastros:locais"))
        self.assertContains(resposta, self.url_local)

    def test_lista_de_equipamentos_liga_para_a_memoria(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(reverse("cadastros:equipamentos"))
        self.assertContains(resposta, self.url_ativo)
