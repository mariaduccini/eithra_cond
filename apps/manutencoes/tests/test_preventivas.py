"""A fundação das manutenções preventivas: o plano, a conta e a ocorrência.

Um plano é a regra que se repete; uma manutenção é o serviço do dia. Aqui se
confere que a regra calcula a próxima data com honestidade — inclusive nos meses
que não têm o dia 31 —, que a ocorrência nasce do plano com o retrato daquele
dia, que a mesma competência não gera duas manutenções e que nada de outro
condomínio atravessa o domínio.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.condominios.constants import Papel
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
