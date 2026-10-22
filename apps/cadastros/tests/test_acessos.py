"""Usuários e acessos: quem entra no condomínio, com que papel e o que fica.

Dar acesso é uma decisão sobre pessoas. A tela pede e o domínio decide — quem
responde pelo condomínio administra, a supervisão consulta. Nada é apagado: o
vínculo é desativado, e o histórico de quem mudou o quê continua legível.

Quem concede o acesso cria a conta, e não escolhe a senha de ninguém: a gestão
entrega um endereço de primeiro acesso e a própria pessoa define a dela.
"""
import re
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts import primeiro_acesso
from apps.cadastros.tests.apoio import (
    SENHA,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
    entrar_como,
)
from apps.condominios.constants import EventoAcesso, Papel
from apps.condominios.models import AcessoUsuarioCondominio, HistoricoAcessoCondominio
from apps.condominios.services import (
    ErroDeAcesso,
    alterar_papel,
    definir_ativo_acesso,
)

Usuario = get_user_model()


class GestaoDeAcessosTests(TestCase):
    """O que a gestão faz na tela de usuários e acessos."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:acessos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    # -- Entrada na tela ------------------------------------------------------

    def test_tela_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_gestao_ve_quem_tem_acesso(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["pode_gerenciar"])

    def test_lista_de_acessos_prepara_tabela_para_textos_longos(self):
        criar_pessoa(
            "uma.pessoa.com.email.bem.grande.para.teste@exemplo.test",
            "Pessoa com nome bem grande para validar a quebra da grade",
        )
        self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "uma.pessoa.com.email.bem.grande.para.teste@exemplo.test",
                "papel": Papel.MORADOR,
            },
        )

        resposta = self.client.get(self.url)

        self.assertContains(resposta, 'class="table align-middle mb-0 eithra-acessos-table"', html=False)
        self.assertContains(resposta, 'class="text-body-secondary small text-break"', html=False)
        self.assertNotContains(resposta, "{# Ações de um acesso na lista.")

    # -- Adicionar acesso -----------------------------------------------------

    def test_adiciona_acesso_a_quem_ja_e_conhecido(self):
        criar_pessoa("porteiro@exemplo.test", "Porteiro do turno")
        resposta = self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "porteiro@exemplo.test",
                "papel": Papel.PORTEIRO,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario__email="porteiro@exemplo.test"
        )
        self.assertEqual(vinculo.papel, Papel.PORTEIRO)
        self.assertTrue(vinculo.ativo)

    def test_adiciona_acesso_criando_identidade_sem_senha(self):
        resposta = self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "nova@exemplo.test",
                "nome_completo": "Pessoa Nova",
                "papel": Papel.OPERACIONAL,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        pessoa = Usuario.objects.get(email="nova@exemplo.test")
        self.assertEqual(pessoa.full_name, "Pessoa Nova")
        # Ninguém define senha por outra pessoa.
        self.assertFalse(pessoa.has_usable_password())
        self.assertTrue(
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, usuario=pessoa
            ).exists()
        )

    def test_email_novo_sem_nome_e_recusado_com_frase(self):
        resposta = self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "sem-nome@exemplo.test",
                "papel": Papel.MORADOR,
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Usuario.objects.filter(email="sem-nome@exemplo.test").exists())

    def test_quem_ja_tem_acesso_nao_e_duplicado(self):
        pessoa = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        resposta = self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "morador@exemplo.test",
                "papel": Papel.MORADOR,
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, usuario=pessoa
            ).count(),
            1,
        )

    def test_vinculo_inativo_e_reativado_com_o_papel_pedido(self):
        pessoa = criar_pessoa("antigo@exemplo.test", "Pessoa Antiga")
        criar_vinculo(
            pessoa, self.condominio, papel=Papel.PORTEIRO, ativo=False
        )
        resposta = self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "antigo@exemplo.test",
                "papel": Papel.SUPERVISORA,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario=pessoa
        )
        self.assertTrue(vinculo.ativo)
        self.assertEqual(vinculo.papel, Papel.SUPERVISORA)
        self.assertEqual(
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, usuario=pessoa
            ).count(),
            1,
        )
        self.assertEqual(
            HistoricoAcessoCondominio.objects.get(acesso=vinculo).evento,
            EventoAcesso.ATIVADO,
        )

    def test_papel_inexistente_e_recusado(self):
        resposta = self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "alguem@exemplo.test",
                "nome_completo": "Alguém",
                "papel": "presidente",
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Usuario.objects.filter(email="alguem@exemplo.test").exists())

    def test_adicionar_acesso_nao_toca_na_senha_de_quem_existe(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "conhecida@exemplo.test",
                "papel": Papel.MORADOR,
            },
        )
        pessoa.refresh_from_db()
        self.assertTrue(pessoa.has_usable_password())
        self.assertTrue(pessoa.check_password(SENHA))

    # -- Mudar papel ----------------------------------------------------------

    def test_muda_o_papel_de_outra_pessoa(self):
        pessoa = criar_pessoa("supervisora@exemplo.test", "Supervisora")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        resposta = self.client.post(
            reverse("cadastros:alterar_papel_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "papel": Papel.SUPERVISORA},
        )
        self.assertEqual(resposta.status_code, 302)
        vinculo.refresh_from_db()
        self.assertEqual(vinculo.papel, Papel.SUPERVISORA)
        historico = HistoricoAcessoCondominio.objects.get(acesso=vinculo)
        self.assertEqual(historico.evento, EventoAcesso.PAPEL_ALTERADO)
        self.assertEqual(historico.dados_anteriores["papel"], Papel.MORADOR)
        self.assertEqual(historico.dados_novos["papel"], Papel.SUPERVISORA)
        self.assertEqual(historico.usuario_executor, self.gestao)

    def test_ninguem_muda_o_proprio_papel(self):
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario=self.gestao
        )
        resposta = self.client.post(
            reverse("cadastros:alterar_papel_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "papel": Papel.ADMIN},
        )
        self.assertEqual(resposta.status_code, 400)
        vinculo.refresh_from_db()
        self.assertEqual(vinculo.papel, Papel.SINDICA)

    # -- Retirar e devolver o acesso -----------------------------------------

    def test_retirar_desativa_o_vinculo_sem_apagar(self):
        pessoa = criar_pessoa("morador@exemplo.test", "Morador")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        resposta = self.client.post(
            reverse("cadastros:alternar_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 302)
        vinculo.refresh_from_db()
        self.assertFalse(vinculo.ativo)
        self.assertTrue(AcessoUsuarioCondominio.objects.filter(pk=vinculo.pk).exists())
        self.assertEqual(
            HistoricoAcessoCondominio.objects.get(acesso=vinculo).evento,
            EventoAcesso.INATIVADO,
        )
        # A conta global continua intacta.
        pessoa.refresh_from_db()
        self.assertTrue(pessoa.is_active)

    def test_devolve_acesso_de_quem_foi_retirado(self):
        pessoa = criar_pessoa("morador@exemplo.test", "Morador")
        vinculo = criar_vinculo(
            pessoa, self.condominio, papel=Papel.MORADOR, ativo=False
        )
        self.client.post(
            reverse("cadastros:alternar_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        vinculo.refresh_from_db()
        self.assertTrue(vinculo.ativo)
        self.assertEqual(
            HistoricoAcessoCondominio.objects.get(acesso=vinculo).evento,
            EventoAcesso.ATIVADO,
        )

    def test_ultimo_gestor_nao_e_retirado_e_a_tela_nao_quebra(self):
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario=self.gestao
        )
        resposta = self.client.post(
            reverse("cadastros:alternar_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 302)
        vinculo.refresh_from_db()
        self.assertTrue(vinculo.ativo)
        self.assertFalse(HistoricoAcessoCondominio.objects.filter(acesso=vinculo).exists())

    # -- Fronteira entre condomínios -----------------------------------------

    def test_vinculo_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        pessoa = criar_pessoa("de-outro@exemplo.test", "Pessoa de Outro")
        alheio = criar_vinculo(pessoa, outro, papel=Papel.MORADOR)
        resposta = self.client.post(
            reverse("cadastros:alternar_acesso", args=[alheio.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 404)
        alheio.refresh_from_db()
        self.assertTrue(alheio.ativo)

    # -- Autorização ----------------------------------------------------------

    def test_supervisora_consulta_mas_nao_gerencia(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(
            self.client.post(
                reverse("cadastros:adicionar_acesso"),
                {
                    "condominio": str(self.condominio.pk),
                    "email": "alguem@exemplo.test",
                    "nome_completo": "Alguém",
                    "papel": Papel.MORADOR,
                },
            ).status_code,
            403,
        )
        self.assertFalse(Usuario.objects.filter(email="alguem@exemplo.test").exists())

    def test_operacional_recebe_404(self):
        entrar_como(
            self.client, Papel.OPERACIONAL, self.condominio, "operacional@exemplo.test"
        )
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("cadastros:adicionar_acesso"),
                {
                    "condominio": str(self.condominio.pk),
                    "email": "alguem@exemplo.test",
                    "nome_completo": "Alguém",
                    "papel": Papel.MORADOR,
                },
            ).status_code,
            404,
        )

    # -- Higiene das rotas ----------------------------------------------------

    def test_rotas_de_acao_nao_aceitam_get(self):
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario=self.gestao
        )
        self.assertEqual(
            self.client.get(reverse("cadastros:adicionar_acesso")).status_code, 405
        )
        self.assertEqual(
            self.client.get(
                reverse("cadastros:alterar_papel_acesso", args=[vinculo.pk])
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                reverse("cadastros:gerar_link_primeiro_acesso", args=[vinculo.pk])
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                reverse("cadastros:alternar_acesso", args=[vinculo.pk])
            ).status_code,
            405,
        )

    def test_post_sem_csrf_e_recusado(self):
        cliente = Client(enforce_csrf_checks=True)
        entrar_como(cliente, Papel.SINDICA, self.condominio, "gestao@exemplo.test")
        resposta = cliente.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": "alguem@exemplo.test",
                "nome_completo": "Alguém",
                "papel": Papel.MORADOR,
            },
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(AcessoUsuarioCondominio.objects.filter(
            usuario__email="alguem@exemplo.test"
        ).exists())


class PrimeiroAcessoPelaGestaoTests(TestCase):
    """Conceder o acesso e entregar o endereço: a senha é de quem o recebe.

    Quem responde pelo condomínio não escolhe senha nenhuma — entrega o
    endereço, e ele vale até a pessoa definir a dela. Quem já tem senha não
    recebe endereço, porque não precisa: já entra.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:acessos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _conceder(self, email, nome="Pessoa Nova", papel=Papel.OPERACIONAL):
        """Dá o acesso pela tela e devolve a página já com o aviso lido."""
        return self.client.post(
            reverse("cadastros:adicionar_acesso"),
            {
                "condominio": str(self.condominio.pk),
                "email": email,
                "nome_completo": nome,
                "papel": papel,
            },
            follow=True,
        )

    def _endereco_no_email(self) -> str:
        """O endereço de primeiro acesso que saiu no último e-mail."""
        self.assertTrue(mail.outbox, "o fluxo deveria ter enviado um e-mail")
        encontrado = re.search(
            r"(https?://\S*primeiro-acesso/\S+)",
            mail.outbox[-1].body,
        )
        self.assertIsNotNone(encontrado, "o e-mail deveria trazer o endereço")
        return encontrado.group(1)

    def _pedir_endereco(self, vinculo):
        """Pede o endereço de novo, como a lista de acessos pede."""
        return self.client.post(
            reverse("cadastros:gerar_link_primeiro_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk)},
            follow=True,
        )

    def test_acesso_novo_vem_com_o_endereco_para_entregar(self):
        resposta = self._conceder("nova@exemplo.test")
        self.assertContains(resposta, "Enviamos as instruções para nova@exemplo.test.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("primeiro-acesso", mail.outbox[0].body)

    def test_o_endereco_entregue_define_a_senha_de_quem_recebeu(self):
        resposta = self._conceder("nova@exemplo.test")
        pessoa = Usuario.objects.get(email="nova@exemplo.test")
        caminho = urlparse(self._endereco_no_email()).path

        self.client.logout()
        self.assertEqual(self.client.get(caminho).status_code, 200)

        senha = "outraSenhaBoa2026"
        definiu = self.client.post(
            caminho, {"senha": senha, "confirmacao": senha}
        )
        self.assertRedirects(definiu, reverse("accounts:login"))

        pessoa.refresh_from_db()
        self.assertTrue(pessoa.check_password(senha))
        # O endereço valeu uma vez: o mesmo não abre mais formulário.
        self.assertEqual(self.client.get(caminho).status_code, 410)

    def test_quem_ja_tem_senha_recebe_acesso_sem_endereco(self):
        criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        resposta = self._conceder("conhecida@exemplo.test", nome="Pessoa Conhecida")
        self.assertContains(resposta, "Acesso concedido.")
        self.assertContains(resposta, "Enviar redefinição de senha")
        self.assertEqual(len(mail.outbox), 0)

    def test_falha_no_envio_do_primeiro_acesso_e_avisada_sem_perder_o_vinculo(self):
        with patch(
            "apps.cadastros.views.primeiro_acesso.enviar_endereco_por_email",
            side_effect=primeiro_acesso.ErroDeEntregaDoPrimeiroAcesso(),
        ):
            resposta = self._conceder("nova@exemplo.test")

        self.assertContains(resposta, "não foi possível preparar as instruções")
        self.assertTrue(
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, usuario__email="nova@exemplo.test"
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_gestao_pede_o_endereco_outra_vez(self):
        self._conceder("nova@exemplo.test")
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario__email="nova@exemplo.test"
        )
        resposta = self._pedir_endereco(vinculo)
        self.assertContains(resposta, "Enviamos as instruções para nova@exemplo.test.")
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("primeiro-acesso", mail.outbox[-1].body)

    def test_quem_ja_tem_senha_tambem_pode_receber_endereco_para_troca(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        resposta = self._pedir_endereco(vinculo)
        self.assertContains(resposta, "Enviamos as instruções para conhecida@exemplo.test.")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("trocar a sua senha", mail.outbox[0].body)

    def test_falha_no_reenvio_mostra_erro_humano(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)

        with patch(
            "apps.cadastros.views.primeiro_acesso.enviar_endereco_por_email",
            side_effect=primeiro_acesso.ErroDeEntregaDoPrimeiroAcesso(),
        ):
            resposta = self._pedir_endereco(vinculo)

        self.assertContains(resposta, "Não foi possível preparar as instruções agora")
        self.assertEqual(len(mail.outbox), 0)

    def test_endereco_gerado_para_quem_ja_tem_senha_permitem_troca(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        self._pedir_endereco(vinculo)
        caminho = urlparse(self._endereco_no_email()).path

        self.client.logout()
        nova_senha = "SenhaTrocadaBoa2026"
        definiu = self.client.post(
            caminho, {"senha": nova_senha, "confirmacao": nova_senha}
        )

        self.assertRedirects(definiu, reverse("accounts:login"))
        pessoa.refresh_from_db()
        self.assertTrue(pessoa.check_password(nova_senha))

    def test_vinculo_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        pessoa = criar_pessoa("de-outro@exemplo.test", "Pessoa de Outro")
        alheio = criar_vinculo(pessoa, outro, papel=Papel.MORADOR)
        self.assertEqual(self._pedir_endereco(alheio).status_code, 404)

    def test_quem_nao_gerencia_nao_pede_o_endereco(self):
        sem_senha = Usuario(email="nova@exemplo.test", full_name="Pessoa Nova")
        sem_senha.set_unusable_password()
        sem_senha.save()
        vinculo = criar_vinculo(sem_senha, self.condominio, papel=Papel.MORADOR)

        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        self.assertEqual(self._pedir_endereco(vinculo).status_code, 403)

        entrar_como(
            self.client, Papel.OPERACIONAL, self.condominio, "operacional@exemplo.test"
        )
        self.assertEqual(self._pedir_endereco(vinculo).status_code, 404)

    def test_a_lista_so_oferece_endereco_a_quem_nao_tem_senha(self):
        self._conceder("nova@exemplo.test", nome="Pessoa Nova")
        por_email = {
            item["email"]: [acao["label"] for acao in item["acoes"]]
            for item in self.client.get(self.url).context["acessos"]
        }
        self.assertEqual(
            por_email["nova@exemplo.test"],
            ["Mudar papel", "Reenviar primeiro acesso", "Retirar acesso"],
        )
        self.assertEqual(
            por_email[self.gestao.email],
            ["Mudar papel", "Enviar redefinição de senha", "Retirar acesso"],
        )

    # -- O que a gestão não faz ----------------------------------------------

    def test_a_gestao_nao_define_senha_de_ninguem(self):
        """Não há caminho administrativo que grave a senha de outra pessoa."""
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        senha_da_pessoa = pessoa.password

        self._pedir_endereco(vinculo)
        self.client.post(
            reverse("cadastros:alterar_papel_acesso", args=[vinculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "papel": Papel.MORADOR,
                "senha": "senhaEscolhidaPelaGestao",
            },
        )

        pessoa.refresh_from_db()
        self.assertEqual(pessoa.password, senha_da_pessoa)
        self.assertFalse(pessoa.check_password("senhaEscolhidaPelaGestao"))

    def test_a_lista_nao_expoe_senha_nem_detalhe_tecnico(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)

        resposta = self.client.get(self.url)

        self.assertNotContains(resposta, pessoa.password)
        self.assertNotContains(resposta, "pbkdf2")
        self.assertNotContains(resposta, "has_usable_password")
        self.assertNotContains(resposta, "set_unusable_password")
        self.assertNotContains(resposta, "uidb64")

    # -- O que fica registrado ------------------------------------------------

    def test_reenviar_o_primeiro_acesso_fica_registrado(self):
        self._conceder("nova@exemplo.test")
        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario__email="nova@exemplo.test"
        )

        self._pedir_endereco(vinculo)

        registros = HistoricoAcessoCondominio.objects.filter(
            acesso=vinculo, evento=EventoAcesso.INSTRUCOES_DE_SENHA_ENVIADAS
        )
        # Dois pedidos: um ao conceder o acesso, outro ao reenviar.
        self.assertEqual(registros.count(), 2)
        for registro in registros:
            self.assertEqual(registro.usuario_executor, self.gestao)
            self.assertEqual(registro.dados_novos["momento"], "primeiro_acesso")

    def test_pedir_redefinicao_fica_registrado_sem_credencial(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)

        self._pedir_endereco(vinculo)

        registro = HistoricoAcessoCondominio.objects.get(
            acesso=vinculo, evento=EventoAcesso.INSTRUCOES_DE_SENHA_ENVIADAS
        )
        self.assertEqual(registro.dados_novos["momento"], "redefinicao")
        conteudo = f"{registro.dados_anteriores}{registro.dados_novos}"
        self.assertNotIn("pbkdf2", conteudo)
        self.assertNotIn("token", conteudo)


class RegrasDeGestaoDoAcessoTests(TestCase):
    """As regras que o domínio garante mesmo sem passar pela tela."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        self.vinculo_gestao = criar_vinculo(
            self.sindica, self.condominio, papel=Papel.SINDICA
        )
        self.executor = criar_pessoa("executor@exemplo.test", "Executor")

    def test_ultimo_gestor_nao_pode_ser_rebaixado(self):
        with self.assertRaises(ErroDeAcesso):
            alterar_papel(
                acesso=self.vinculo_gestao,
                papel=Papel.MORADOR,
                executor=self.executor,
            )
        self.vinculo_gestao.refresh_from_db()
        self.assertEqual(self.vinculo_gestao.papel, Papel.SINDICA)

    def test_ultimo_gestor_nao_pode_ser_retirado(self):
        with self.assertRaises(ErroDeAcesso):
            definir_ativo_acesso(
                acesso=self.vinculo_gestao, ativo=False, executor=self.executor
            )
        self.vinculo_gestao.refresh_from_db()
        self.assertTrue(self.vinculo_gestao.ativo)

    def test_rebaixar_gestor_com_outro_gestor_ativo_e_permitido(self):
        outro = criar_pessoa("outro-admin@exemplo.test", "Outro Administrador")
        criar_vinculo(outro, self.condominio, papel=Papel.ADMIN)
        alterar_papel(
            acesso=self.vinculo_gestao, papel=Papel.MORADOR, executor=self.executor
        )
        self.vinculo_gestao.refresh_from_db()
        self.assertEqual(self.vinculo_gestao.papel, Papel.MORADOR)

    def test_operacao_sem_executor_e_recusada(self):
        with self.assertRaises(ErroDeAcesso):
            definir_ativo_acesso(
                acesso=self.vinculo_gestao, ativo=False, executor=None
            )
