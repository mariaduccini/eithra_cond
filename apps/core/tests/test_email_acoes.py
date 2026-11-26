"""Testes da confirmação humana das propostas lidas de e-mail.

A pergunta desta fase é uma só: o que acontece quando alguém confirma, na tela,
uma ação que a leitura sugeriu ontem? A resposta que estes testes cobram é
sempre a mesma —

    nada é executado a partir de uma proposta guardada.

Confirmar obriga o backend a recuperar a mensagem de novo, reinterpretar,
reconciliar com o contexto de agora e só então — se a ação ainda corresponder à
proposta atual — chamar um service do domínio. O provedor do Gmail e o serviço
de linguagem são sempre falsos: nenhum teste toca a rede.
"""
from unittest.mock import patch

from django.test import Client, override_settings
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_pessoa, criar_vinculo
from apps.condominios.constants import Papel
from apps.core import email_acoes, gmail
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
)
from apps.core.email_interpretacao import AcaoProposta, Consequencia
from apps.core.models import ItemImportacaoHistorica, VinculoImportacaoHistorica
from apps.core.tests.test_leitura_email import (
    CORPO_SIGILOSO,
    BaseLeituraTests,
    ProvedorFalso,
    criar_item,
    mensagem,
    resposta,
)
from apps.demandas.constants import OrigemDemanda, StatusDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import StatusManutencao

# O que cada mensagem de teste diz — e o que a leitura entende dela.
OCORRENCIA = "Vazamento de água na garagem."
ASSUNTO_DA_OCORRENCIA = "Vazamento na garagem"
RECADO = "Obrigado, recebido."
ELEVADOR = "O elevador social parou de funcionar."
ELEVADOR_REPETIDO = "O elevador social parou de funcionar novamente."
ASSUNTO_DO_ELEVADOR = "Elevador social parado"

LLM = "apps.core.email_interpretacao.llm.pedir_json"
FABRICA_DO_PROVEDOR = "apps.core.gmail.obter_provedor"
MENSAGEM_DE_CONTEXTO_MUDOU = "O contexto mudou desde a análise. Revise a nova proposta."


def patchar_leitura(**campos):
    """Congela o serviço de linguagem — nenhum teste chama o serviço real."""
    campos.setdefault("consequencia", "nova_ocorrencia")
    return patch(LLM, return_value=resposta(**campos))


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@override_settings(EITHRA_GMAIL_ATIVO=True)
class BaseConfirmacaoTests(BaseLeituraTests):
    """Condomínio, gestão com papel de decisão e as ferramentas da confirmação."""

    def setUp(self):
        super().setUp()
        self.sindica = criar_pessoa("sindica.decisao@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)
        self.cliente = Client()

    # --- leitura e confirmação -------------------------------------------------

    def levar_para_confirmacao(self, item, provedor):
        """Roda a leitura com o provedor falso — sem tocar o Gmail."""
        provedor.item_pk = item.pk
        with patchar_leitura():
            self.processar(provedor, item)
        item.refresh_from_db()
        return item

    def aguardando(self, provedor, *, assunto=ASSUNTO_DA_OCORRENCIA, **kwargs):
        """Um item novo, já lido e aguardando decisão humana."""
        return self.levar_para_confirmacao(
            criar_item(self.condominio, assunto=assunto, **kwargs), provedor
        )

    def confirmar(self, item, acao, provedor):
        """Chama a camada de execução com o provedor falso."""
        with patchar_leitura():
            return email_acoes.confirmar_acao_email(
                item, acao, usuario=self.sindica, provedor=provedor
            )

    def confirmar_pela_tela(self, item, acao, provedor, **extra):
        """Confirma pela view, com a fábrica de provedor trocada pelo falso."""
        self.entrar()
        with patch(FABRICA_DO_PROVEDOR, return_value=provedor), patchar_leitura():
            return self.cliente.post(
                reverse("core:confirmar", args=[item.pk]),
                {"acao": acao, **extra},
            )

    def entrar(self):
        self.cliente.force_login(self.sindica)
        return self.sindica

    def aguardando_sem_leitura(self, *, assunto=""):
        """Um item levado à mão até ``AGUARDANDO_CONFIRMACAO``, pela via válida."""
        item = criar_item(self.condominio, assunto=assunto)
        objetos = ItemImportacaoHistorica.objects
        item = objetos.alterar_estado_processamento(
            item=item, novo_estado=EstadoProcessamentoImportacao.PROCESSANDO
        )
        return objetos.alterar_estado_processamento(
            item=item, novo_estado=EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )

    # --- cenários recorrentes --------------------------------------------------

    def com_demanda_do_elevador(self, provedor):
        """Um item apontando para a demanda aberta do elevador."""
        elevador = self.equipamento("Elevador social")
        demanda = self.demanda("Elevador social parado", equipamento=elevador)
        item = self.aguardando(
            provedor, assunto=ASSUNTO_DO_ELEVADOR
        )
        return item, demanda

    def com_manutencao_do_elevador(self, provedor):
        """Um item apontando para a manutenção aberta do elevador."""
        elevador = self.equipamento("Elevador social")
        manutencao = self.manutencao("Ajuste do elevador social", equipamento=elevador)
        item = self.aguardando(provedor, assunto=ASSUNTO_DO_ELEVADOR)
        return item, manutencao


def provedor_da_ocorrencia(texto=OCORRENCIA, assunto=ASSUNTO_DA_OCORRENCIA):
    return ProvedorFalso(mensagem=mensagem(texto=texto, assunto=assunto))


def provedor_do_elevador(texto=ELEVADOR):
    return ProvedorFalso(mensagem=mensagem(texto=texto, assunto=ASSUNTO_DO_ELEVADOR))


# ---------------------------------------------------------------------------
# 1-4. Acesso e alcance
# ---------------------------------------------------------------------------


class AcessoTests(BaseConfirmacaoTests):
    def test_confirmacao_exige_login(self):
        """1. Sem sessão, nem a leitura nem a confirmação são alcançadas."""
        item = self.aguardando(provedor_da_ocorrencia())

        sem_sessao = self.cliente.get(reverse("core:confirmacao", args=[item.pk]))
        self.assertEqual(sem_sessao.status_code, 302)

        envio = self.cliente.post(
            reverse("core:confirmar", args=[item.pk]),
            {"acao": AcaoProposta.CRIAR_NOVA_DEMANDA},
        )
        self.assertEqual(envio.status_code, 302)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_usuario_sem_papel_de_decisao_e_recusado(self):
        """2. Quem não decide sobre o condomínio não alcança o item."""
        item = self.aguardando(provedor_da_ocorrencia())
        portaria = criar_pessoa("portaria.decisao@exemplo.test", "Portaria")
        criar_vinculo(portaria, self.condominio, papel=Papel.PORTEIRO)
        self.cliente.force_login(portaria)

        resposta_http = self.cliente.get(reverse("core:confirmacao", args=[item.pk]))
        self.assertEqual(resposta_http.status_code, 404)

    def test_item_de_outro_condominio_e_recusado(self):
        """3. Um item de condomínio alheio não é alcançável."""
        item = self.levar_para_confirmacao(
            criar_item(self.outro, assunto=ASSUNTO_DA_OCORRENCIA),
            provedor_da_ocorrencia(),
        )
        self.entrar()

        resposta_http = self.cliente.get(reverse("core:confirmacao", args=[item.pk]))
        self.assertEqual(resposta_http.status_code, 404)

    def test_item_fora_da_confirmacao_e_recusado(self):
        """4. Só o que está aguardando confirmação aparece para decidir."""
        pendente = criar_item(self.condominio, assunto="Ainda não lida")
        self.entrar()

        resposta_http = self.cliente.get(reverse("core:confirmacao", args=[pendente.pk]))
        self.assertEqual(resposta_http.status_code, 404)


# ---------------------------------------------------------------------------
# 5-9. Reconstrução obrigatória
# ---------------------------------------------------------------------------


class ReconstrucaoTests(BaseConfirmacaoTests):
    def test_confirmacao_recupera_a_mensagem_de_novo(self):
        """5. A confirmação volta ao Gmail pelo ``message_id``."""
        provedor = provedor_da_ocorrencia()
        item = self.aguardando(provedor)
        provedor.mensagens_pedidas.clear()

        self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor)

        self.assertEqual(provedor.mensagens_pedidas, [item.message_id])

    def test_confirmacao_reinterpreta_a_mensagem(self):
        """6. O que vale é a leitura de agora, não a de ontem."""
        item = self.aguardando(provedor_da_ocorrencia())
        hoje = ProvedorFalso(mensagem=mensagem(texto=RECADO, assunto="Agradecimento"))

        resultado = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, hoje)

        self.assertIsNotNone(resultado.leitura)
        self.assertEqual(resultado.leitura.tipo_de_consequencia, Consequencia.SEM_ACAO)

    def test_confirmacao_reconcilia_com_o_contexto_de_agora(self):
        """7. O contexto é consultado de novo, no mesmo condomínio."""
        elevador = self.equipamento("Elevador social")
        item = self.aguardando(provedor_do_elevador())
        demanda = self.demanda("Elevador social parado", equipamento=elevador)

        resultado = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_do_elevador())

        self.assertTrue(resultado.contexto_mudou)
        self.assertEqual(resultado.leitura.proposta.acao, AcaoProposta.RELACIONAR_DEMANDA)
        self.assertEqual(resultado.leitura.proposta.candidato.objeto, demanda)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_proposta_guardada_nao_e_executada(self):
        """8. A proposta não fica gravada no item: nada a executar de memória."""
        item = self.aguardando(provedor_da_ocorrencia())

        campos = " ".join(
            str(valor)
            for valor in ItemImportacaoHistorica.objects.get(pk=item.pk).__dict__.values()
        )
        self.assertNotIn("Considerar uma nova demanda", campos)

        resultado = self.confirmar(
            item,
            AcaoProposta.CRIAR_NOVA_DEMANDA,
            ProvedorFalso(mensagem=mensagem(texto=RECADO, assunto="Agradecimento")),
        )
        self.assertFalse(resultado.executado)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_proposta_alterada_nao_executa_a_acao_antiga(self):
        """9. Se a mensagem deixou de pedir aquilo, a ação antiga não roda."""
        item = self.aguardando(provedor_da_ocorrencia())

        resultado = self.confirmar(
            item,
            AcaoProposta.CRIAR_NOVA_DEMANDA,
            ProvedorFalso(mensagem=mensagem(texto=RECADO, assunto="Agradecimento")),
        )

        self.assertTrue(resultado.contexto_mudou)
        self.assertFalse(resultado.executado)
        self.assertEqual(Demanda.objects.count(), 0)
        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento,
            EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
        )


# ---------------------------------------------------------------------------
# 10-11. Relacionar demanda
# ---------------------------------------------------------------------------


class RelacionarDemandaTests(BaseConfirmacaoTests):
    def test_relacionar_demanda_cria_vinculo(self):
        """10. A consequência é a proveniência: fica o vínculo com a demanda."""
        item, demanda = self.com_demanda_do_elevador(provedor_do_elevador(ELEVADOR_REPETIDO))

        resultado = self.confirmar(item, AcaoProposta.RELACIONAR_DEMANDA, provedor_do_elevador(ELEVADOR_REPETIDO))

        self.assertTrue(resultado.executado)
        vinculo = VinculoImportacaoHistorica.objects.get(item=item)
        self.assertEqual(vinculo.entidade_destino, EntidadeImportacaoHistorica.DEMANDA)
        self.assertEqual(vinculo.destino_id, demanda.pk)
        self.assertTrue(vinculo.revisao_manual)

    def test_relacionar_demanda_nao_altera_o_status(self):
        """11. Relacionar não edita a demanda só para marcar que chegou e-mail."""
        item, demanda = self.com_demanda_do_elevador(provedor_do_elevador(ELEVADOR_REPETIDO))

        self.confirmar(item, AcaoProposta.RELACIONAR_DEMANDA, provedor_do_elevador(ELEVADOR_REPETIDO))

        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)


# ---------------------------------------------------------------------------
# 12-13. Relacionar manutenção
# ---------------------------------------------------------------------------


class RelacionarManutencaoTests(BaseConfirmacaoTests):
    def test_relacionar_manutencao_cria_vinculo(self):
        """12. Mesmo princípio: proveniência, sem tocar no serviço."""
        item, manutencao = self.com_manutencao_do_elevador(provedor_do_elevador())

        resultado = self.confirmar(item, AcaoProposta.RELACIONAR_MANUTENCAO, provedor_do_elevador())

        self.assertTrue(resultado.executado)
        vinculo = VinculoImportacaoHistorica.objects.get(item=item)
        self.assertEqual(vinculo.entidade_destino, EntidadeImportacaoHistorica.MANUTENCAO)
        self.assertEqual(vinculo.destino_id, manutencao.pk)
        self.assertTrue(vinculo.revisao_manual)

    def test_relacionar_manutencao_nao_conclui(self):
        """13. "O fornecedor concluiu" continua sendo declaração."""
        item, manutencao = self.com_manutencao_do_elevador(provedor_do_elevador())

        self.confirmar(item, AcaoProposta.RELACIONAR_MANUTENCAO, provedor_do_elevador())

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_conclusao)
        self.assertFalse(manutencao.esta_encerrada)


# ---------------------------------------------------------------------------
# 14-17. Nova demanda
# ---------------------------------------------------------------------------


class CriarNovaDemandaTests(BaseConfirmacaoTests):
    def _kwargs_da_criacao(self, item, provedor):
        """Roda a confirmação observando como o service do domínio foi chamado."""
        from apps.demandas import services as demandas_services

        with patch.object(
            demandas_services, "criar_demanda", wraps=demandas_services.criar_demanda
        ) as criar:
            self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor)
        return criar.call_args

    def test_nova_demanda_exige_confirmacao_explicita(self):
        """14. A leitura propõe; quem cria é o clique."""
        item = self.aguardando(provedor_da_ocorrencia())
        self.assertEqual(Demanda.objects.count(), 0)

        resultado = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_da_ocorrencia())

        self.assertTrue(resultado.executado)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_nova_demanda_usa_o_service_do_dominio(self):
        """15. Quem registra é ``apps.demandas.services.criar_demanda``."""
        item = self.aguardando(provedor_da_ocorrencia())
        chamada = self._kwargs_da_criacao(item, provedor_da_ocorrencia())

        self.assertIsNotNone(chamada)
        self.assertEqual(chamada.kwargs["origem"], OrigemDemanda.EMAIL)
        self.assertEqual(chamada.kwargs["condominio"], self.condominio)
        self.assertEqual(chamada.kwargs["criado_por"], self.sindica)

    def test_nova_demanda_nao_recebe_status_manual(self):
        """16. O status é regra do domínio, não escolha da confirmação."""
        item = self.aguardando(provedor_da_ocorrencia())
        chamada = self._kwargs_da_criacao(item, provedor_da_ocorrencia())

        self.assertNotIn("status", chamada.kwargs)
        self.assertEqual(Demanda.objects.get().status, StatusDemanda.ABERTA)

    def test_nova_demanda_nao_recebe_prioridade_manual(self):
        """17. A prioridade é consequência do relato, lida pela regra do registro."""
        item = self.aguardando(provedor_da_ocorrencia())
        chamada = self._kwargs_da_criacao(item, provedor_da_ocorrencia())

        self.assertNotIn("prioridade", chamada.kwargs)


# ---------------------------------------------------------------------------
# 18-19. Idempotência
# ---------------------------------------------------------------------------


class IdempotenciaTests(BaseConfirmacaoTests):
    def _reabrir_e_reler(self, item):
        """Reprocessa o item, como um retry faria, e o devolve lido de novo."""
        ItemImportacaoHistorica.objects.reprocessar_item(item=item)
        item.refresh_from_db()
        provedor = provedor_da_ocorrencia()
        provedor.item_pk = item.pk
        with patchar_leitura():
            self.processar(provedor, item)
        item.refresh_from_db()
        return item

    def test_dupla_confirmacao_nao_cria_segunda_demanda(self):
        """18. Confirmar duas vezes não gera uma segunda consequência."""
        item = self.aguardando(provedor_da_ocorrencia())
        primeira = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_da_ocorrencia())
        self.assertEqual(Demanda.objects.count(), 1)

        item = self._reabrir_e_reler(item)
        self.assertEqual(
            item.estado_processamento,
            EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
        )

        segunda = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_da_ocorrencia())

        self.assertTrue(segunda.executado)
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(segunda.destino.pk, primeira.destino.pk)

    def test_dupla_confirmacao_nao_duplica_vinculo(self):
        """19. O vínculo já registrado é reaproveitado, não recriado."""
        item = self.aguardando(provedor_da_ocorrencia())
        self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_da_ocorrencia())

        item = self._reabrir_e_reler(item)
        self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_da_ocorrencia())

        self.assertEqual(
            VinculoImportacaoHistorica.objects.filter(
                item=item,
                entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
            ).count(),
            1,
        )


# ---------------------------------------------------------------------------
# 20-22. Estado do item e contexto
# ---------------------------------------------------------------------------


class EstadoDoItemTests(BaseConfirmacaoTests):
    def test_acao_concluida_leva_o_item_a_processado(self):
        """20. Depois da consequência, o item sai da fila de decisão."""
        item = self.aguardando(provedor_da_ocorrencia())

        resultado = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_da_ocorrencia())

        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.PROCESSADO)
        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento, EstadoProcessamentoImportacao.PROCESSADO
        )
        self.assertIsNotNone(item.processado_em)

    def test_falha_tecnica_nao_marca_processado(self):
        """21. Falha técnica vira ``ERRO`` — nunca sucesso."""
        item = self.aguardando(provedor_da_ocorrencia())
        quebrado = ProvedorFalso(erro=gmail.GmailMensagemInexistente())

        resultado = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, quebrado)

        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.ERRO)
        self.assertFalse(resultado.executado)
        item.refresh_from_db()
        self.assertEqual(item.estado_processamento, EstadoProcessamentoImportacao.ERRO)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_contexto_alterado_avisa_e_nao_executa(self):
        """22. O contexto mudou: a leitura nova volta para ser revista."""
        item = self.aguardando(provedor_da_ocorrencia())

        resultado = self.confirmar(
            item,
            AcaoProposta.CRIAR_NOVA_DEMANDA,
            ProvedorFalso(mensagem=mensagem(texto=RECADO, assunto="Agradecimento")),
        )

        self.assertTrue(resultado.contexto_mudou)
        self.assertEqual(resultado.mensagem, MENSAGEM_DE_CONTEXTO_MUDOU)
        self.assertIsNotNone(resultado.leitura)


# ---------------------------------------------------------------------------
# 23-26. Segurança
# ---------------------------------------------------------------------------


class SegurancaTests(BaseConfirmacaoTests):
    def test_payload_forjado_de_destino_e_ignorado(self):
        """23. O destino vem da proposta atual, não do que a tela mandou."""
        item, demanda = self.com_demanda_do_elevador(provedor_do_elevador(ELEVADOR_REPETIDO))
        forjada = self.demanda("Demanda de outro condomínio", condominio=self.outro)
        provedor = provedor_do_elevador(ELEVADOR_REPETIDO)

        resposta_http = self.confirmar_pela_tela(
            item,
            AcaoProposta.RELACIONAR_DEMANDA,
            provedor,
            destino=str(forjada.pk),
            destino_id=str(forjada.pk),
        )

        self.assertEqual(resposta_http.status_code, 302)
        vinculo = VinculoImportacaoHistorica.objects.get(item=item)
        self.assertEqual(vinculo.destino_id, demanda.pk)
        self.assertNotEqual(vinculo.destino_id, forjada.pk)

    def test_candidato_de_outro_condominio_nunca_e_usado(self):
        """24. O contexto de um condomínio não atravessa para o outro."""
        elevador = self.equipamento("Elevador social")
        elevador_alheio = self.equipamento("Elevador social", condominio=self.outro)
        self.demanda(
            "Elevador social parado",
            condominio=self.outro,
            equipamento=elevador_alheio,
        )
        item = self.aguardando(provedor_do_elevador())

        resultado = self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor_do_elevador())

        self.assertTrue(resultado.executado)
        self.assertEqual(resultado.destino.condominio_id, self.condominio.pk)
        self.assertEqual(resultado.destino.equipamento_id, elevador.pk)
        self.assertEqual(Demanda.objects.filter(condominio=self.outro).count(), 1)
        self.assertEqual(Demanda.objects.filter(condominio=self.condominio).count(), 1)
        vinculo = VinculoImportacaoHistorica.objects.get(item=item)
        self.assertEqual(vinculo.destino_id, resultado.destino.pk)

    def test_get_nunca_executa_a_acao(self):
        """25. Nenhuma consequência acontece por GET."""
        item = self.aguardando(provedor_da_ocorrencia())
        self.entrar()

        resposta_http = self.cliente.get(reverse("core:confirmar", args=[item.pk]))

        self.assertEqual(resposta_http.status_code, 405)
        self.assertEqual(Demanda.objects.count(), 0)
        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento,
            EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
        )

    def test_post_sem_csrf_e_recusado(self):
        """26. Sem token, a confirmação não passa."""
        item = self.aguardando(provedor_da_ocorrencia())
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.sindica)

        resposta_http = cliente.post(
            reverse("core:confirmar", args=[item.pk]),
            {"acao": AcaoProposta.CRIAR_NOVA_DEMANDA},
        )

        self.assertEqual(resposta_http.status_code, 403)
        self.assertEqual(Demanda.objects.count(), 0)


# ---------------------------------------------------------------------------
# 27-30. Higiene: nada vaza, nada conclui sozinho, nada chama o provedor real
# ---------------------------------------------------------------------------


class HigieneTests(BaseConfirmacaoTests):
    def test_nenhum_corpo_do_email_e_persistido(self):
        """27. O corpo participa da leitura e desaparece."""
        testemunho = CORPO_SIGILOSO
        provedor = ProvedorFalso(
            mensagem=mensagem(texto=OCORRENCIA + " " + testemunho, assunto=ASSUNTO_DA_OCORRENCIA)
        )
        item = self.aguardando(provedor)

        self.confirmar(item, AcaoProposta.CRIAR_NOVA_DEMANDA, provedor)

        campos = " ".join(
            str(valor)
            for valor in ItemImportacaoHistorica.objects.get(pk=item.pk).__dict__.values()
        )
        self.assertNotIn(testemunho, campos)
        self.assertNotIn(testemunho, str(VinculoImportacaoHistorica.objects.all()))
        self.assertNotIn(testemunho, str(Demanda.objects.all()))

    def test_a_tela_nao_mostra_detalhe_tecnico(self):
        """28. A decisão aparece em linguagem humana, sem nada de sistema."""
        provedor = provedor_da_ocorrencia()
        item = self.aguardando(provedor)
        self.entrar()

        with patch(FABRICA_DO_PROVEDOR, return_value=provedor), patchar_leitura():
            resposta_http = self.cliente.get(reverse("core:confirmacao", args=[item.pk]))

        self.assertEqual(resposta_http.status_code, 200)
        self.assertContains(resposta_http, "EITHRA identificou")
        for termo in ("message_id", "score", "prompt", "consequencia", "json"):
            self.assertNotContains(resposta_http, termo)

    def test_nenhuma_conclusao_automatica_acontece(self):
        """29. Ação fora do escopo desta fase continua sendo decisão do módulo."""
        elevador = self.equipamento("Elevador social")
        manutencao = self.manutencao("Ajuste do elevador social", equipamento=elevador)
        item = self.aguardando(
            provedor_do_elevador("O elevador social foi concluído."),
            assunto=ASSUNTO_DO_ELEVADOR,
        )

        leitura, _ = email_acoes.leitura_atual(item, provedor=provedor_do_elevador("O elevador social foi concluído."))
        self.assertEqual(leitura.proposta.acao, AcaoProposta.AVALIAR_CONCLUSAO)
        self.assertEqual(email_acoes.acao_disponivel(leitura), "")

        with self.assertRaises(email_acoes.AcaoNaoImplementada):
            self.confirmar(item, AcaoProposta.AVALIAR_CONCLUSAO, provedor_do_elevador())

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertFalse(manutencao.esta_encerrada)
        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento,
            EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
        )

    def test_nenhum_provedor_real_e_chamado(self):
        """30. Com o provedor injetado, a fábrica real nunca entra em cena."""
        item = self.aguardando_sem_leitura(assunto="Agradecimento")
        provedor = ProvedorFalso(mensagem=mensagem(texto=RECADO, assunto="Agradecimento"))

        with patch(FABRICA_DO_PROVEDOR, side_effect=AssertionError("provedor real em uso")):
            with patchar_leitura():
                resultado = email_acoes.confirmar_acao_email(
                    item, AcaoProposta.NENHUMA, usuario=self.sindica, provedor=provedor
                )

        self.assertTrue(resultado.executado)
        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.PROCESSADO)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)
