"""Testes da proveniência e idempotência da carga histórica.

A fonte externa tem identidade própria e o domínio tem a dele. Os testes daqui
separam as duas coisas: recarregar a mesma fonte não duplica a fonte, e a mesma
fonte pode explicar vários registros do Eithra sem virar várias fontes.
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)


class LoteImportacaoHistoricaTests(TestCase):
    """O lote guarda a origem da rodada sem depender dela para sempre."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao.carga@exemplo.test", "Gestão da carga")

    def test_hash_da_fonte_precisa_ter_formato_sha256(self):
        lote = LoteImportacaoHistorica(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.DRIVE,
            hash_fonte="abc",
        )

        with self.assertRaises(ValidationError):
            lote.full_clean()

    def test_hash_da_fonte_e_guardado_normalizado(self):
        lote = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.DRIVE,
            hash_fonte="A" * 64,
            criado_por=self.gestao,
        )

        self.assertEqual(lote.hash_fonte, "a" * 64)


class ItemImportacaoHistoricaTests(TestCase):
    """O item é a identidade da fonte: isolado por condomínio e idempotente."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro_condominio = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao.carga@exemplo.test", "Gestão da carga")
        self.lote_gmail = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            conta_origem="sindica@exemplo.test",
            criado_por=self.gestao,
        )
        self.lote_gmail_reprocessado = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            conta_origem="sindica@exemplo.test",
            criado_por=self.gestao,
        )
        self.lote_drive = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.DRIVE,
            nome_original="Lista RVE atualizada.xlsx",
            criado_por=self.gestao,
        )
        self.lote_drive_reprocessado = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.DRIVE,
            nome_original="Lista RVE atualizada (corrigida).xlsx",
            criado_por=self.gestao,
        )
        self.lote_outro_condominio = LoteImportacaoHistorica.objects.create(
            condominio=self.outro_condominio,
            origem=OrigemImportacaoHistorica.DRIVE,
            nome_original="Lista RVE atualizada.xlsx",
            criado_por=self.gestao,
        )

    def _mensagem(self, *, lote=None, identificador="msg-001", **campos):
        return ItemImportacaoHistorica.objects.registrar_item(
            lote=lote or self.lote_gmail,
            identificador_externo=identificador,
            message_id=campos.pop("message_id", identificador),
            criado_por=self.gestao,
            **campos,
        )

    def _arquivo(self, *, lote=None, identificador="arquivo-drive-001", **campos):
        return ItemImportacaoHistorica.objects.registrar_item(
            lote=lote or self.lote_drive,
            identificador_externo=identificador,
            file_id=campos.pop("file_id", identificador),
            criado_por=self.gestao,
            **campos,
        )

    def test_reprocessar_a_mesma_mensagem_nao_duplica_a_fonte(self):
        """A. A mesma mensagem do Gmail reprocessada continua sendo um item."""
        primeiro, criado = self._mensagem()
        segundo, criado_de_novo = self._mensagem(
            lote=self.lote_gmail_reprocessado, thread_id="thr-001"
        )

        self.assertTrue(criado)
        self.assertFalse(criado_de_novo)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(segundo.lote, self.lote_gmail_reprocessado)
        self.assertEqual(segundo.thread_id, "thr-001")

    def test_reprocessar_o_mesmo_arquivo_nao_duplica_a_fonte(self):
        """C. O mesmo file_id do Drive reprocessado continua sendo um item."""
        primeiro, _ = self._arquivo()
        segundo, criado_de_novo = self._arquivo(lote=self.lote_drive_reprocessado)

        self.assertFalse(criado_de_novo)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)

    def test_isolamento_por_condominio_permite_mesma_chave_externa(self):
        self._arquivo()
        outro, criado = ItemImportacaoHistorica.objects.registrar_item(
            lote=self.lote_outro_condominio,
            identificador_externo="arquivo-drive-001",
            file_id="arquivo-drive-001",
            criado_por=self.gestao,
        )

        self.assertTrue(criado)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(outro.condominio, self.outro_condominio)

    def test_conta_declarada_faz_parte_da_identidade_da_fonte(self):
        self._mensagem()

        outra_conta, criado = ItemImportacaoHistorica.objects.registrar_item(
            lote=self.lote_gmail,
            identificador_externo="msg-001",
            message_id="msg-001",
            conta_origem="outra.conta@exemplo.test",
            criado_por=self.gestao,
        )

        self.assertTrue(criado)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(outra_conta.conta_origem, "outra.conta@exemplo.test")

    def test_mesmo_binario_em_duas_fontes_mantem_as_duas_proveniencias(self):
        """D. O mesmo PDF pode ter vindo do Gmail e do Drive: são duas origens."""
        resumo = "b" * 64
        do_gmail, _ = self._mensagem(
            identificador="msg-777", hash_fonte=resumo.upper()
        )
        do_drive, _ = self._arquivo(
            identificador="arquivo-drive-777", hash_fonte=resumo
        )

        self.assertNotEqual(do_gmail.pk, do_drive.pk)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(do_gmail.hash_fonte, resumo)
        self.assertEqual(do_drive.hash_fonte, resumo)
        self.assertEqual(
            set(ItemImportacaoHistorica.objects.values_list("origem", flat=True)),
            {OrigemImportacaoHistorica.GMAIL, OrigemImportacaoHistorica.DRIVE},
        )

    def test_item_sem_identificador_externo_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._arquivo(identificador="   ")

    def test_item_do_gmail_exige_message_id(self):
        item = ItemImportacaoHistorica(
            lote=self.lote_gmail,
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            identificador_externo="gmail:sem-message-id",
        )

        with self.assertRaises(ValidationError):
            item.full_clean()


class VinculoImportacaoHistoricaTests(TestCase):
    """O vínculo diz para onde a fonte aponta, e pode haver mais de um."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro_condominio = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao.carga@exemplo.test", "Gestão da carga")
        self.lote = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            conta_origem="sindica@exemplo.test",
            criado_por=self.gestao,
        )
        self.lote_reprocessado = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            conta_origem="sindica@exemplo.test",
            criado_por=self.gestao,
        )
        self.item, _ = ItemImportacaoHistorica.objects.registrar_item(
            lote=self.lote,
            identificador_externo="msg-500",
            message_id="msg-500",
            criado_por=self.gestao,
        )

    def test_a_mesma_fonte_pode_explicar_varios_registros_do_dominio(self):
        """B. Uma mensagem pode ser demanda e manutenção sem virar duas fontes."""
        self.item, _ = ItemImportacaoHistorica.objects.registrar_item(
            lote=self.lote_reprocessado,
            identificador_externo="msg-500",
            message_id="msg-500",
            criado_por=self.gestao,
        )
        demanda, primeira = VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=self.item,
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
            resultado=ResultadoImportacaoHistorica.CRIADO,
            criado_por=self.gestao,
        )
        manutencao, segunda = VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=self.item,
            entidade_destino=EntidadeImportacaoHistorica.MANUTENCAO,
            resultado=ResultadoImportacaoHistorica.CRIADO,
            criado_por=self.gestao,
        )

        self.assertTrue(primeira)
        self.assertTrue(segunda)
        self.assertNotEqual(demanda.pk, manutencao.pk)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 2)
        self.assertEqual(
            set(self.item.vinculos.values_list("entidade_destino", flat=True)),
            {
                EntidadeImportacaoHistorica.DEMANDA,
                EntidadeImportacaoHistorica.MANUTENCAO,
            },
        )

    def test_reprocessamento_atualiza_o_vinculo_sem_duplicar(self):
        primeiro, _ = VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=self.item,
            entidade_destino=EntidadeImportacaoHistorica.DOCUMENTO_OPERACIONAL,
            resultado=ResultadoImportacaoHistorica.PENDENTE,
            revisao_manual=True,
            observacao="Origem ainda não conferida.",
            criado_por=self.gestao,
        )

        segundo, criado = VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=self.item,
            entidade_destino=EntidadeImportacaoHistorica.DOCUMENTO_OPERACIONAL,
            resultado=ResultadoImportacaoHistorica.CRIADO,
            observacao="Documento localizado no anexo.",
            criado_por=self.gestao,
        )

        self.assertFalse(criado)
        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 1)
        self.assertEqual(segundo.resultado, ResultadoImportacaoHistorica.CRIADO)
        self.assertFalse(segundo.revisao_manual)
        self.assertEqual(
            segundo.observacao, "Documento localizado no anexo."
        )

    def test_revisao_manual_e_marcador_complementar(self):
        vinculo, _ = VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=self.item,
            entidade_destino=EntidadeImportacaoHistorica.MANUTENCAO,
            resultado=ResultadoImportacaoHistorica.PENDENTE,
            revisao_manual=True,
            observacao="Conclusão não identificada nas fontes analisadas.",
            criado_por=self.gestao,
        )

        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.PENDENTE)
        self.assertTrue(vinculo.revisao_manual)
        self.assertEqual(
            list(
                VinculoImportacaoHistorica.objects.do_condominio(self.condominio)
                .pendentes_de_revisao()
            ),
            [vinculo],
        )

    def test_vinculo_nao_atravessa_condominio(self):
        vinculo = VinculoImportacaoHistorica(
            item=self.item,
            condominio=self.outro_condominio,
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
        )

        with self.assertRaises(ValidationError):
            vinculo.full_clean()
