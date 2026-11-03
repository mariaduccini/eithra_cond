"""A fundação das manutenções preventivas: o plano, a conta e a ocorrência.

Um plano é a regra que se repete; uma manutenção é o serviço do dia. Aqui se
confere que a regra calcula a próxima data com honestidade — inclusive nos meses
que não têm o dia 31 —, que a ocorrência nasce do plano com o retrato daquele
dia, que a mesma competência não gera duas manutenções e que nada de outro
condomínio atravessa o domínio.
"""
from datetime import date
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.condominios.constants import Papel
from apps.core.automacao import ErroDeAutomacao, resolver_ator_automatico
from apps.core.constants import Prioridade
from apps.manutencoes.constants import (
    FrequenciaUnidade,
    StatusManutencao,
    TipoEventoManutencao,
    TipoManutencao,
)
from apps.manutencoes.models import HistoricoManutencao, Manutencao, PlanoPreventivo
from apps.manutencoes.periodicidade import proxima_data
from apps.manutencoes.services import (
    ErroDeManutencao,
    criar_manutencao,
    gerar_ocorrencia_preventiva,
    gerar_preventivas_pendentes,
    reconciliar_competencia_preventiva,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_demanda,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)


def _plano(condominio, criado_por, **campos):
    """Um plano válido do condomínio, com o que o teste quiser trocar."""
    base = {
        "titulo": "Revisão da bomba de recalque",
        "descricao": "Revisar o conjunto e trocar o selo mecânico.",
        "frequencia_unidade": FrequenciaUnidade.MESES,
        "frequencia_intervalo": 3,
        "proxima_data_prevista": date(2026, 11, 15),
        "criado_por": criado_por,
    }
    base.update(campos)
    plano = PlanoPreventivo(condominio=condominio, **base)
    plano.full_clean()
    plano.save()
    return plano


class PlanoTests(TestCase):
    """O plano diz o que fazer, onde e de quanto em quanto — e nada de outro condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_plano_valido_e_guardado(self):
        plano = _plano(self.condominio, self.gestao)
        self.assertEqual(plano.titulo, "Revisão da bomba de recalque")
        self.assertTrue(plano.ativo)
        self.assertEqual(plano.frequencia_unidade, FrequenciaUnidade.MESES)
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    def test_titulo_vazio_e_recusado(self):
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="   ",
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_intervalo_menor_que_um_e_recusado(self):
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="A cada nada",
                frequencia_intervalo=0,
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_local_de_outro_condominio_e_recusado(self):
        alheio = criar_local(self.outro, "Garagem do Beta")
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="Plano",
                local=alheio,
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_equipamento_de_outro_condominio_e_recusado(self):
        alheio = criar_equipamento(self.outro, "Bomba do Beta")
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="Plano",
                equipamento=alheio,
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_fornecedor_de_outro_condominio_e_recusado(self):
        alheio = criar_fornecedor(self.outro, "Fornecedor do Beta")
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="Plano",
                fornecedor=alheio,
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_responsavel_sem_vinculo_com_o_condominio_e_recusado(self):
        de_fora = criar_pessoa("fora@exemplo.test", "De fora")
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="Plano",
                responsavel=de_fora,
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_local_divergente_do_equipamento_e_recusado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        outro_local = criar_local(self.condominio, "Telhado")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        with self.assertRaises(ValidationError):
            PlanoPreventivo(
                condominio=self.condominio,
                titulo="Plano",
                equipamento=equipamento,
                local=outro_local,
                proxima_data_prevista=date(2026, 11, 15),
                criado_por=self.gestao,
            ).full_clean()

    def test_plano_inativo_e_aceito_e_continua_consultavel(self):
        plano = _plano(self.condominio, self.gestao, ativo=False)
        self.assertFalse(plano.ativo)
        self.assertEqual(PlanoPreventivo.objects.filter(pk=plano.pk).count(), 1)

    def test_leitura_por_condominio_nao_ve_o_plano_do_outro(self):
        _plano(self.condominio, self.gestao)
        self.assertEqual(
            PlanoPreventivo.objects.do_condominio(self.outro).count(), 0
        )
        self.assertEqual(
            PlanoPreventivo.objects.do_condominio(self.condominio).count(), 1
        )

    def test_ativos_deixa_de_fora_o_plano_desativado(self):
        _plano(self.condominio, self.gestao)
        _plano(self.condominio, self.gestao, ativo=False)
        self.assertEqual(PlanoPreventivo.objects.ativos().count(), 1)


class PeriodicidadeTests(TestCase):
    """A conta da próxima data, sozinha, incluindo os meses que não têm o dia."""

    def test_a_cada_trinta_dias(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.DIAS,
            intervalo=30,
            a_partir_de=date(2026, 11, 15),
        )
        self.assertEqual(resultado, date(2026, 12, 15))

    def test_a_cada_um_mes(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.MESES,
            intervalo=1,
            a_partir_de=date(2026, 11, 15),
        )
        self.assertEqual(resultado, date(2026, 12, 15))

    def test_a_cada_tres_meses(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.MESES,
            intervalo=3,
            a_partir_de=date(2026, 11, 15),
        )
        self.assertEqual(resultado, date(2027, 2, 15))

    def test_a_cada_um_ano(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.ANOS,
            intervalo=1,
            a_partir_de=date(2026, 11, 15),
        )
        self.assertEqual(resultado, date(2027, 11, 15))

    def test_dia_31_em_mes_curto_cai_no_ultimo_dia(self):
        """31/01 mais um mês é 28/02, e não uma data que não existe."""
        resultado = proxima_data(
            unidade=FrequenciaUnidade.MESES,
            intervalo=1,
            a_partir_de=date(2026, 1, 31),
        )
        self.assertEqual(resultado, date(2026, 2, 28))

    def test_31_de_janeiro_em_ano_bissexto_cai_no_29(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.MESES,
            intervalo=1,
            a_partir_de=date(2024, 1, 31),
        )
        self.assertEqual(resultado, date(2024, 2, 29))

    def test_dia_31_em_mes_de_trinta(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.MESES,
            intervalo=1,
            a_partir_de=date(2026, 3, 31),
        )
        self.assertEqual(resultado, date(2026, 4, 30))

    def test_29_de_fevereiro_mais_um_ano_cai_no_28(self):
        resultado = proxima_data(
            unidade=FrequenciaUnidade.ANOS,
            intervalo=1,
            a_partir_de=date(2024, 2, 29),
        )
        self.assertEqual(resultado, date(2025, 2, 28))


class GeracaoTests(TestCase):
    """O plano vira ocorrência: uma só por competência, com retrato e com histórico."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.local = criar_local(self.condominio, "Casa de máquinas")
        self.equipamento = criar_equipamento(
            self.condominio, "Bomba de recalque", local=self.local
        )
        self.fornecedor = criar_fornecedor(self.condominio, "Bombas Ltda")
        self.plano = _plano(
            self.condominio,
            self.gestao,
            local=self.local,
            equipamento=self.equipamento,
            fornecedor=self.fornecedor,
            responsavel=self.gestao,
        )

    def test_gera_uma_manutencao_preventiva(self):
        manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        self.assertEqual(manutencao.tipo, TipoManutencao.PREVENTIVA)
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertEqual(manutencao.prioridade, Prioridade.NORMAL)
        self.assertEqual(Manutencao.objects.count(), 1)

    def test_ocorrencia_fica_vinculada_ao_plano(self):
        manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        self.assertEqual(manutencao.plano_preventivo_id, self.plano.pk)
        self.assertEqual(self.plano.ocorrencias.count(), 1)

    def test_contexto_do_plano_e_copiado(self):
        manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        self.assertEqual(manutencao.titulo, self.plano.titulo)
        self.assertEqual(manutencao.descricao_problema, self.plano.descricao)
        self.assertEqual(manutencao.local_id, self.local.pk)
        self.assertEqual(manutencao.equipamento_id, self.equipamento.pk)
        self.assertEqual(manutencao.fornecedor_id, self.fornecedor.pk)
        self.assertEqual(manutencao.responsavel_id, self.gestao.pk)
        self.assertEqual(manutencao.condominio_id, self.condominio.pk)

    def test_data_prevista_e_a_competencia_do_plano(self):
        manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        self.assertEqual(manutencao.data_prevista, date(2026, 11, 15))
        self.assertEqual(manutencao.competencia_preventiva, date(2026, 11, 15))

    def test_historico_registra_a_criacao_pelo_plano(self):
        manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        evento = manutencao.historico.get()
        self.assertEqual(evento.tipo_evento, TipoEventoManutencao.CRIADA)
        self.assertEqual(evento.usuario, self.gestao)
        # O plano aparece pelo nome, e não por um identificador técnico.
        self.assertIn(self.plano.titulo, evento.descricao)
        self.assertNotIn(str(self.plano.pk), evento.descricao)

    def test_proxima_data_avanca_conforme_a_frequencia(self):
        gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)
        self.plano.refresh_from_db()
        self.assertEqual(self.plano.proxima_data_prevista, date(2027, 2, 15))

    def test_plano_inativo_nao_gera_e_falha(self):
        self.plano.ativo = False
        self.plano.save(update_fields=["ativo"])
        with self.assertRaises(ErroDeManutencao):
            gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)

        self.assertEqual(Manutencao.objects.count(), 0)
        self.plano.refresh_from_db()
        # A data do plano inativo não anda quando a geração é recusada.
        self.assertEqual(self.plano.proxima_data_prevista, date(2026, 11, 15))

    def test_segunda_execucao_para_a_mesma_competencia_nao_duplica(self):
        primeira = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        # A competência de novembro já gerou; voltar a pedir a mesma data não
        # cria uma segunda ocorrência.
        PlanoPreventivo.objects.filter(pk=self.plano.pk).update(
            proxima_data_prevista=date(2026, 11, 15)
        )
        segunda = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        self.assertEqual(segunda.pk, primeira.pk)
        self.assertEqual(Manutencao.objects.count(), 1)

    def test_constraint_protege_competencia_duplicada(self):
        gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Manutencao.objects.create(
                    condominio=self.condominio,
                    plano_preventivo=self.plano,
                    competencia_preventiva=date(2026, 11, 15),
                    tipo=TipoManutencao.PREVENTIVA,
                    titulo="Duplicada",
                    data_prevista=date(2026, 11, 15),
                    criado_por=self.gestao,
                )

    def test_falha_ao_registrar_desfaz_a_geracao(self):
        from apps.manutencoes import services

        original = services._registrar

        def _explode(*args, **kwargs):
            raise RuntimeError("falha proposital")

        services._registrar = _explode
        try:
            with self.assertRaises(RuntimeError):
                services.gerar_ocorrencia_preventiva(
                    plano=self.plano, usuario=self.gestao
                )
        finally:
            services._registrar = original

        self.assertEqual(Manutencao.objects.count(), 0)
        self.assertEqual(HistoricoManutencao.objects.count(), 0)
        self.plano.refresh_from_db()
        # Nada de parcial: a data do plano continua onde estava.
        self.assertEqual(self.plano.proxima_data_prevista, date(2026, 11, 15))

    def test_alteracao_do_plano_nao_e_retroativa(self):
        ocorrencia = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        self.plano.titulo = "Revisão anual da bomba"
        self.plano.descricao = "Outro escopo, decidido depois."
        self.plano.save()

        ocorrencia.refresh_from_db()
        self.assertEqual(ocorrencia.titulo, "Revisão da bomba de recalque")
        self.assertEqual(
            ocorrencia.descricao_problema, "Revisar o conjunto e trocar o selo mecânico."
        )

        # A próxima geração já copia o plano como ele está agora.
        seguinte = gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)
        self.assertEqual(seguinte.titulo, "Revisão anual da bomba")

    def test_mudanca_de_periodicidade_nao_recalcula_o_passado(self):
        primeira = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )
        # Depois da primeira ocorrência, a próxima data vigente é 15/02/2027,
        # calculada ainda pela frequência antiga (três meses).
        self.assertEqual(self.plano.proxima_data_prevista, date(2027, 2, 15))

        # A gestão decide que a revisão passa a ser anual — a decisão vale do
        # próximo avanço em diante, e não reescreve o que já nasceu.
        self.plano.frequencia_unidade = FrequenciaUnidade.ANOS
        self.plano.frequencia_intervalo = 1
        self.plano.save()

        seguinte = gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)

        self.assertEqual(seguinte.competencia_preventiva, date(2027, 2, 15))
        self.plano.refresh_from_db()
        self.assertEqual(self.plano.proxima_data_prevista, date(2028, 2, 15))

        primeira.refresh_from_db()
        self.assertEqual(primeira.data_prevista, date(2026, 11, 15))
        self.assertEqual(primeira.competencia_preventiva, date(2026, 11, 15))

    def test_competencia_vencida_permanece_identificavel(self):
        """Uma execução atrasada gera a competência devida, sem pulá-la."""
        gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)
        vencida = Manutencao.objects.get(
            plano_preventivo=self.plano, competencia_preventiva=date(2026, 11, 15)
        )
        self.assertEqual(vencida.data_prevista, date(2026, 11, 15))
        self.plano.refresh_from_db()
        self.assertEqual(self.plano.proxima_data_prevista, date(2027, 2, 15))

    def test_gerar_para_plano_inativo_nao_toca_no_que_existe(self):
        gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)
        self.plano.ativo = False
        self.plano.save(update_fields=["ativo"])

        with self.assertRaises(ErroDeManutencao):
            gerar_ocorrencia_preventiva(plano=self.plano, usuario=self.gestao)

        self.assertEqual(Manutencao.objects.count(), 1)
        self.assertEqual(self.plano.ocorrencias.count(), 1)


class InvariantesTests(TestCase):
    """Nenhum fluxo interno fura o isolamento nem cria uma ocorrência incoerente."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.sindico_do_outro = criar_pessoa("outro@exemplo.test", "Síndico do Beta")
        criar_vinculo(self.sindico_do_outro, self.outro, Papel.SINDICA)
        self.plano_do_outro = _plano(self.outro, self.sindico_do_outro)

    def _ocorrencia(self, **campos):
        base = {
            "condominio": self.condominio,
            "titulo": "Ocorrência",
            "criado_por": self.gestao,
        }
        base.update(campos)
        return Manutencao(**base)

    def test_manutencao_com_plano_de_outro_condominio_e_recusada(self):
        with self.assertRaises(ValidationError):
            self._ocorrencia(
                plano_preventivo=self.plano_do_outro,
                competencia_preventiva=date(2026, 11, 15),
                tipo=TipoManutencao.PREVENTIVA,
                data_prevista=date(2026, 11, 15),
            ).full_clean()

    def test_manutencao_com_plano_sem_competencia_e_recusada(self):
        plano = _plano(self.condominio, self.gestao)
        with self.assertRaises(ValidationError):
            self._ocorrencia(
                plano_preventivo=plano,
                tipo=TipoManutencao.PREVENTIVA,
            ).full_clean()

    def test_manutencao_com_plano_e_corretiva_e_recusada(self):
        plano = _plano(self.condominio, self.gestao)
        with self.assertRaises(ValidationError):
            self._ocorrencia(
                plano_preventivo=plano,
                competencia_preventiva=date(2026, 11, 15),
                tipo=TipoManutencao.CORRETIVA,
            ).full_clean()

    def test_manutencao_preventiva_com_plano_e_demanda_e_recusada(self):
        plano = _plano(self.condominio, self.gestao)
        demanda = criar_demanda(self.condominio, self.gestao, "Chamado")
        with self.assertRaises(ValidationError):
            self._ocorrencia(
                plano_preventivo=plano,
                competencia_preventiva=date(2026, 11, 15),
                tipo=TipoManutencao.PREVENTIVA,
                demanda_origem=demanda,
            ).full_clean()

    def test_competencia_sem_plano_e_recusada(self):
        with self.assertRaises(ValidationError):
            self._ocorrencia(
                competencia_preventiva=date(2026, 11, 15),
            ).full_clean()


class CompatibilidadeTests(TestCase):
    """O que já existia continua igual: corretiva, demanda de origem e o vínculo 1:N."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_corretiva_avulsa_continua_funcionando(self):
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        self.assertEqual(manutencao.tipo, TipoManutencao.CORRETIVA)
        self.assertIsNone(manutencao.demanda_origem_id)
        self.assertIsNone(manutencao.plano_preventivo_id)
        self.assertIsNone(manutencao.competencia_preventiva)

    def test_corretiva_por_demanda_mantem_a_origem_intacta(self):
        demanda = criar_demanda(self.condominio, self.gestao, "Vazamento")
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Reparar o vazamento",
            demanda_origem=demanda,
        )
        self.assertEqual(manutencao.demanda_origem_id, demanda.pk)
        self.assertIsNone(manutencao.plano_preventivo_id)
        self.assertIn(demanda.titulo, manutencao.historico.get().descricao)

    def test_demanda_gera_varias_manutencoes(self):
        demanda = criar_demanda(self.condominio, self.gestao, "Reforma")
        criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Primeira frente",
            demanda_origem=demanda,
        )
        criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Segunda frente",
            demanda_origem=demanda,
        )
        self.assertEqual(demanda.manutencoes_geradas.count(), 2)


class GeracaoPeriodicaTests(TestCase):
    """O gerador periódico encontra os planos vencidos e gera o que ficou devendo."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.sindico_do_outro = criar_pessoa("outro@exemplo.test", "Síndico do Beta")
        criar_vinculo(self.sindico_do_outro, self.outro, Papel.SINDICA)

    def _gerar(self, referencia, **campos):
        return gerar_preventivas_pendentes(
            usuario=self.gestao, data_referencia=referencia, **campos
        )

    def test_plano_vencido_gera_ocorrencia(self):
        plano = _plano(self.condominio, self.gestao)
        resumo = self._gerar(date(2026, 11, 20))

        self.assertEqual(resumo.ocorrencias_geradas, 1)
        self.assertEqual(resumo.planos_processados, 1)
        self.assertEqual(Manutencao.objects.count(), 1)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2027, 2, 15))

    def test_plano_de_hoje_gera_ocorrencia(self):
        _plano(self.condominio, self.gestao)
        resumo = self._gerar(date(2026, 11, 15))
        self.assertEqual(resumo.ocorrencias_geradas, 1)

    def test_plano_futuro_nao_gera(self):
        _plano(self.condominio, self.gestao)
        resumo = self._gerar(date(2026, 11, 1))

        self.assertEqual(resumo.ocorrencias_geradas, 0)
        self.assertEqual(resumo.planos_verificados, 1)
        self.assertEqual(resumo.planos_sem_necessidade, 1)
        self.assertEqual(Manutencao.objects.count(), 0)

    def test_plano_inativo_nao_gera_nem_avanca(self):
        plano = _plano(self.condominio, self.gestao, ativo=False)
        resumo = self._gerar(date(2026, 11, 20))

        self.assertEqual(resumo.planos_verificados, 0)
        self.assertEqual(resumo.ocorrencias_geradas, 0)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    def test_recupera_tres_competencias_atrasadas_em_ordem(self):
        plano = _plano(
            self.condominio,
            self.gestao,
            frequencia_unidade=FrequenciaUnidade.MESES,
            frequencia_intervalo=1,
            proxima_data_prevista=date(2026, 8, 15),
        )
        resumo = self._gerar(date(2026, 10, 20))

        competencias = list(
            Manutencao.objects.filter(plano_preventivo=plano)
            .order_by("competencia_preventiva")
            .values_list("competencia_preventiva", flat=True)
        )
        self.assertEqual(
            competencias,
            [date(2026, 8, 15), date(2026, 9, 15), date(2026, 10, 15)],
        )
        self.assertEqual(resumo.ocorrencias_geradas, 3)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    def test_rodar_duas_vezes_nao_duplica(self):
        plano = _plano(self.condominio, self.gestao)
        self._gerar(date(2026, 11, 20))
        plano.refresh_from_db()
        data_apos_primeira = plano.proxima_data_prevista

        segundo = self._gerar(date(2026, 11, 20))

        self.assertEqual(segundo.ocorrencias_geradas, 0)
        self.assertEqual(Manutencao.objects.count(), 1)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, data_apos_primeira)

    def test_competencia_ja_existente_nao_duplica_nem_trava(self):
        plano = _plano(self.condominio, self.gestao)
        self._gerar(date(2026, 11, 20))
        # A competência de novembro já gerou. Se a data do plano voltar a
        # apontar para ela, o gerador reconhece a ocorrência existente, não cria
        # uma segunda e adianta o plano para a próxima competência — em vez de
        # girar na mesma data ou ficar preso nela.
        PlanoPreventivo.objects.filter(pk=plano.pk).update(
            proxima_data_prevista=date(2026, 11, 15)
        )

        resumo = self._gerar(date(2026, 11, 20))

        self.assertEqual(resumo.ocorrencias_geradas, 0)
        self.assertEqual(resumo.competencias_reconciliadas, 1)
        self.assertEqual(Manutencao.objects.count(), 1)
        plano.refresh_from_db()
        # O plano saiu da competência materializada e seguiu para a seguinte.
        self.assertEqual(plano.proxima_data_prevista, date(2027, 2, 15))

    def test_escopo_por_condominio_nao_alcanca_o_outro(self):
        _plano(self.condominio, self.gestao)
        plano_do_outro = _plano(self.outro, self.sindico_do_outro)

        resumo = self._gerar(date(2026, 11, 20), condominio=self.condominio)

        self.assertEqual(resumo.ocorrencias_geradas, 1)
        self.assertEqual(
            Manutencao.objects.filter(condominio=self.condominio).count(), 1
        )
        self.assertEqual(Manutencao.objects.filter(condominio=self.outro).count(), 0)
        plano_do_outro.refresh_from_db()
        self.assertEqual(plano_do_outro.proxima_data_prevista, date(2026, 11, 15))

    def test_rodada_global_alcanca_todos_os_condominios(self):
        _plano(self.condominio, self.gestao)
        _plano(self.outro, self.sindico_do_outro)
        resumo = self._gerar(date(2026, 11, 20))
        self.assertEqual(resumo.ocorrencias_geradas, 2)

    @override_settings(MANUTENCOES_MAX_OCORRENCIAS_RECUPERADAS_POR_PLANO=3)
    def test_limite_defensivo_interrompe_e_reporta(self):
        plano = _plano(
            self.condominio,
            self.gestao,
            frequencia_unidade=FrequenciaUnidade.MESES,
            frequencia_intervalo=1,
            proxima_data_prevista=date(2026, 1, 15),
        )
        resumo = self._gerar(date(2026, 12, 20))

        self.assertEqual(resumo.ocorrencias_geradas, 3)
        self.assertEqual(resumo.planos_limitados, [str(plano.pk)])
        plano.refresh_from_db()
        # Parou no limite, sem adiantar a data por conta própria: ainda deve as
        # competências de maio em diante, e a próxima rodada continua de lá.
        self.assertEqual(plano.proxima_data_prevista, date(2026, 4, 15))

    def test_falha_em_um_plano_nao_impede_os_demais(self):
        from apps.manutencoes import services

        bom = _plano(self.condominio, self.gestao, titulo="Bomba boa")
        ruim = _plano(self.condominio, self.gestao, titulo="Plano com defeito")

        original = services.gerar_ocorrencia_preventiva

        def _explode(*, plano, usuario):
            if plano.pk == ruim.pk:
                raise RuntimeError("falha proposital")
            return original(plano=plano, usuario=usuario)

        services.gerar_ocorrencia_preventiva = _explode
        try:
            resumo = services.gerar_preventivas_pendentes(
                usuario=self.gestao, data_referencia=date(2026, 11, 20)
            )
        finally:
            services.gerar_ocorrencia_preventiva = original

        self.assertEqual(resumo.ocorrencias_geradas, 1)
        self.assertEqual(len(resumo.falhas), 1)
        self.assertEqual(resumo.falhas[0][0], str(ruim.pk))
        self.assertEqual(Manutencao.objects.count(), 1)
        self.assertEqual(Manutencao.objects.get().plano_preventivo_id, bom.pk)


class ComandoGerarPreventivasTests(TestCase):
    """O comando é só a casca: lê parâmetros, chama o serviço e relata."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_comando_gera_e_relata_sem_traceback(self):
        _plano(self.condominio, self.gestao)
        saida = StringIO()

        call_command(
            "gerar_preventivas",
            "--usuario",
            "gestao@exemplo.test",
            "--data",
            "2026-11-20",
            stdout=saida,
        )

        texto = saida.getvalue()
        self.assertIn("Ocorrências geradas: 1", texto)
        self.assertNotIn("Traceback", texto)
        self.assertEqual(Manutencao.objects.count(), 1)

    def test_data_invalida_e_recusada(self):
        with self.assertRaises(CommandError):
            call_command(
                "gerar_preventivas",
                "--usuario",
                "gestao@exemplo.test",
                "--data",
                "ontem",
                stdout=StringIO(),
            )

    def test_usuario_inexistente_e_recusado(self):
        with self.assertRaises(CommandError):
            call_command(
                "gerar_preventivas",
                "--usuario",
                "ninguem@exemplo.test",
                "--data",
                "2026-11-20",
                stdout=StringIO(),
            )

    def test_condominio_limita_o_escopo(self):
        outro = criar_condominio("Beta")
        sindico = criar_pessoa("outro@exemplo.test", "Síndico do Beta")
        criar_vinculo(sindico, outro, Papel.SINDICA)
        _plano(self.condominio, self.gestao)
        _plano(outro, sindico)

        call_command(
            "gerar_preventivas",
            "--usuario",
            "gestao@exemplo.test",
            "--data",
            "2026-11-20",
            "--condominio",
            str(self.condominio.pk),
            stdout=StringIO(),
        )

        self.assertEqual(
            Manutencao.objects.filter(condominio=self.condominio).count(), 1
        )
        self.assertEqual(Manutencao.objects.filter(condominio=outro).count(), 0)


class ReconciliacaoTests(TestCase):
    """O plano que aponta para uma competência já materializada destrava sozinho.

    Uma ocorrência pode já existir para a competência que o plano ainda aponta —
    criada antes, por outra rodada, ou por qualquer caminho. Se o gerador apenas
    devolvesse a ocorrência existente sem mexer no ponteiro, o plano ficaria
    preso ali para sempre e todas as competências seguintes se perderiam. Aqui se
    confere que a competência já materializada é reconhecida, que o ponteiro anda
    exatamente uma competência por vez e que nada é duplicado nem pulado.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def _plano_mensal(self, **campos):
        base = {
            "frequencia_unidade": FrequenciaUnidade.MESES,
            "frequencia_intervalo": 1,
        }
        base.update(campos)
        return _plano(self.condominio, self.gestao, **base)

    def _materializar(self, plano, *competencias):
        """Registra ocorrências já existentes, sem mexer no ponteiro do plano."""
        for competencia in competencias:
            Manutencao.objects.create(
                condominio=plano.condominio,
                plano_preventivo=plano,
                competencia_preventiva=competencia,
                tipo=TipoManutencao.PREVENTIVA,
                titulo=plano.titulo,
                data_prevista=competencia,
                criado_por=self.gestao,
            )

    def _gerar(self, referencia, **campos):
        return gerar_preventivas_pendentes(
            usuario=self.gestao, data_referencia=referencia, **campos
        )

    def test_competencia_existente_avanca_o_plano(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15))

        resumo = self._gerar(date(2026, 8, 20))

        self.assertEqual(resumo.ocorrencias_geradas, 0)
        self.assertEqual(resumo.competencias_reconciliadas, 1)
        self.assertEqual(Manutencao.objects.count(), 1)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 9, 15))

    def test_duas_competencias_existentes_consecutivas_sao_reconciliadas(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15), date(2026, 9, 15))

        resumo = self._gerar(date(2026, 9, 20))

        self.assertEqual(resumo.ocorrencias_geradas, 0)
        self.assertEqual(resumo.competencias_reconciliadas, 2)
        self.assertEqual(Manutencao.objects.count(), 2)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 10, 15))

    def test_depois_das_existentes_a_competencia_faltante_e_gerada(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15), date(2026, 9, 15))

        resumo = self._gerar(date(2026, 11, 3))

        self.assertEqual(resumo.competencias_reconciliadas, 2)
        self.assertEqual(resumo.ocorrencias_geradas, 1)
        competencias = list(
            Manutencao.objects.filter(plano_preventivo=plano)
            .order_by("competencia_preventiva")
            .values_list("competencia_preventiva", flat=True)
        )
        self.assertEqual(
            competencias,
            [date(2026, 8, 15), date(2026, 9, 15), date(2026, 10, 15)],
        )
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    def test_nenhuma_competencia_pulada_nem_duplicada(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15), date(2026, 9, 15))

        self._gerar(date(2026, 11, 3))

        competencias = list(
            Manutencao.objects.filter(plano_preventivo=plano)
            .order_by("competencia_preventiva")
            .values_list("competencia_preventiva", flat=True)
        )
        self.assertEqual(len(competencias), len(set(competencias)))
        self.assertEqual(
            competencias,
            [date(2026, 8, 15), date(2026, 9, 15), date(2026, 10, 15)],
        )

    def test_ocorrencia_existente_nao_e_modificada(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15))
        existente = Manutencao.objects.get(plano_preventivo=plano)

        def _retrato(ocorrencia):
            return (
                ocorrencia.titulo,
                ocorrencia.data_prevista,
                ocorrencia.competencia_preventiva,
                ocorrencia.status,
                ocorrencia.criado_por_id,
            )

        antes = _retrato(existente)

        self._gerar(date(2026, 8, 20))

        existente.refresh_from_db()
        self.assertEqual(_retrato(existente), antes)

    def test_reconciliacao_nao_escreve_historico(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15))
        self.assertEqual(HistoricoManutencao.objects.count(), 0)

        self._gerar(date(2026, 8, 20))

        # Reconhecer a competência só move o ponteiro do plano: nada aconteceu a
        # uma ocorrência e ninguém agiu, então não há evento a registrar.
        self.assertEqual(HistoricoManutencao.objects.count(), 0)

    def test_nao_avanca_sem_ocorrencia_correspondente(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))

        seguinte = reconciliar_competencia_preventiva(plano=plano)

        self.assertIsNone(seguinte)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 8, 15))

    def test_plano_nao_fica_preso_na_competencia_materializada(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 10, 15))
        self._materializar(plano, date(2026, 10, 15))

        resumo = self._gerar(date(2026, 11, 3))

        self.assertEqual(resumo.competencias_reconciliadas, 1)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    def test_rodar_de_novo_mantem_o_resultado(self):
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15), date(2026, 9, 15))

        self._gerar(date(2026, 11, 3))
        plano.refresh_from_db()
        data_apos_primeira = plano.proxima_data_prevista

        segundo = self._gerar(date(2026, 11, 3))

        self.assertEqual(segundo.ocorrencias_geradas, 0)
        self.assertEqual(segundo.competencias_reconciliadas, 0)
        self.assertEqual(Manutencao.objects.count(), 3)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, data_apos_primeira)

    def test_reconciliacao_nao_avanca_a_mesma_competencia_duas_vezes(self):
        """Duas chamadas que viram a mesma competência antiga avançam só uma vez.

        É o que a trava por plano garante: quem chega depois relê a data vigente
        no banco e não repete o passo que o primeiro já deu.
        """
        plano = self._plano_mensal(proxima_data_prevista=date(2026, 8, 15))
        self._materializar(plano, date(2026, 8, 15))
        # O objeto em memória continua apontando para 15/08, como um segundo
        # chamador que leu a data antes de o primeiro avançar.
        antigo = plano

        primeira = reconciliar_competencia_preventiva(plano=antigo)
        segunda = reconciliar_competencia_preventiva(plano=antigo)

        self.assertEqual(primeira, date(2026, 9, 15))
        self.assertIsNone(segunda)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 9, 15))


class AtorAutomaticoTests(TestCase):
    """Quem assina a geração: a pessoa declarada ou o ator técnico configurado.

    A geração automática não pode ser assinada por quem não fez nada. Quando
    ninguém é declarado, quem assina é o ator técnico do ambiente; se ele não
    estiver configurado ou não existir, nada é gerado e o motivo é dito — nunca
    se escolhe uma pessoa por conta própria.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_usuario_informado_assina_a_geracao(self):
        _plano(self.condominio, self.gestao)

        call_command(
            "gerar_preventivas",
            "--usuario",
            "gestao@exemplo.test",
            "--data",
            "2026-11-20",
            stdout=StringIO(),
        )

        manutencao = Manutencao.objects.get()
        self.assertEqual(manutencao.criado_por, self.gestao)
        self.assertEqual(manutencao.historico.get().usuario, self.gestao)

    @override_settings(EITHRA_AUTOMATION_USER_EMAIL="eithra-automacao@exemplo.test")
    def test_sem_usuario_assina_o_ator_configurado(self):
        robo = criar_pessoa("eithra-automacao@exemplo.test", "Eithra / Automação")
        _plano(self.condominio, self.gestao)

        call_command("gerar_preventivas", "--data", "2026-11-20", stdout=StringIO())

        manutencao = Manutencao.objects.get()
        self.assertEqual(manutencao.criado_por, robo)
        self.assertEqual(manutencao.historico.get().usuario, robo)

    @override_settings(EITHRA_AUTOMATION_USER_EMAIL="eithra-automacao@exemplo.test")
    def test_servico_resolve_o_ator_quando_ninguem_e_declarado(self):
        robo = criar_pessoa("eithra-automacao@exemplo.test", "Eithra / Automação")
        _plano(self.condominio, self.gestao)

        resumo = gerar_preventivas_pendentes(data_referencia=date(2026, 11, 20))

        self.assertEqual(resumo.ocorrencias_geradas, 1)
        self.assertEqual(Manutencao.objects.get().criado_por, robo)

    @override_settings(EITHRA_AUTOMATION_USER_EMAIL="")
    def test_sem_ator_configurado_falha_e_nao_gera(self):
        plano = _plano(self.condominio, self.gestao)

        with self.assertRaises(CommandError):
            call_command("gerar_preventivas", "--data", "2026-11-20", stdout=StringIO())

        self.assertEqual(Manutencao.objects.count(), 0)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    @override_settings(EITHRA_AUTOMATION_USER_EMAIL="")
    def test_sem_ator_nao_escolhe_pessoa_por_conta_propria(self):
        """Mesmo havendo uma pessoa no condomínio, ninguém é escolhido por ela."""
        _plano(self.condominio, self.gestao)

        with self.assertRaises(CommandError):
            call_command("gerar_preventivas", "--data", "2026-11-20", stdout=StringIO())

        self.assertEqual(Manutencao.objects.count(), 0)

    @override_settings(EITHRA_AUTOMATION_USER_EMAIL="ninguem@exemplo.test")
    def test_ator_configurado_mas_inexistente_falha(self):
        plano = _plano(self.condominio, self.gestao)

        with self.assertRaises(CommandError):
            call_command("gerar_preventivas", "--data", "2026-11-20", stdout=StringIO())

        self.assertEqual(Manutencao.objects.count(), 0)
        plano.refresh_from_db()
        self.assertEqual(plano.proxima_data_prevista, date(2026, 11, 15))

    @override_settings(EITHRA_AUTOMATION_USER_EMAIL="")
    def test_resolucao_do_ator_recusa_sem_configuracao(self):
        with self.assertRaises(ErroDeAutomacao):
            resolver_ator_automatico()
