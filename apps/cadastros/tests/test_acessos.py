"""Usuários e acessos: quem entra no condomínio, com que papel e o que fica.

Dar acesso é uma decisão sobre pessoas. A tela pede e o domínio decide — quem
responde pelo condomínio administra, a supervisão consulta. Nada é apagado: o
vínculo é desativado, e o histórico de quem mudou o quê continua legível.

Quem concede o acesso cria a conta e escolhe como a senha nasce: a gestão
entrega um endereço de primeiro acesso, e a própria pessoa define a dela; ou
quem responde pelo condomínio define uma agora, para quando não houver e-mail
que entregue o endereço. Quem decide isso é a permissão de gestão do domínio,
e não um atributo técnico de administração.

A foto é opcional, conferida pelo conteúdo real e substituível a qualquer
momento. Sem foto, a pessoa aparece pelas iniciais.
"""
import re
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts import avatar, primeiro_acesso
from apps.balancos.tests.apoio import StorageDeMentira, jpg, png
from apps.cadastros.forms import SENHA_DA_GESTAO, SENHA_DA_PROPRIA_PESSOA
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
    definir_senha_de_acesso,
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

    # -- Editar o acesso ------------------------------------------------------

    def test_muda_o_papel_de_outra_pessoa(self):
        pessoa = criar_pessoa("supervisora@exemplo.test", "Supervisora")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        resposta = self.client.post(
            reverse("cadastros:editar_acesso", args=[vinculo.pk]),
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
            reverse("cadastros:editar_acesso", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "papel": Papel.ADMINISTRADORA},
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
                reverse("cadastros:editar_acesso", args=[vinculo.pk])
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
        self.assertEqual(
            self.client.get(
                reverse("cadastros:definir_senha_acesso", args=[vinculo.pk])
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
            [
                "Editar acesso",
                "Redefinir senha",
                "Reenviar primeiro acesso",
                "Retirar acesso",
            ],
        )
        self.assertEqual(
            por_email[self.gestao.email],
            [
                "Editar acesso",
                "Redefinir senha",
                "Enviar redefinição de senha",
                "Retirar acesso",
            ],
        )

    # -- O que a gestão não faz ----------------------------------------------

    def test_mudar_papel_nunca_grava_senha(self):
        """Não há atalho: mudar o papel de alguém não encosta na senha dele."""
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        senha_da_pessoa = pessoa.password

        self._pedir_endereco(vinculo)
        self.client.post(
            reverse("cadastros:editar_acesso", args=[vinculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "papel": Papel.MORADOR,
                "senha": "senhaEscolhidaPelaGestao",
            },
        )

        pessoa.refresh_from_db()
        self.assertEqual(pessoa.password, senha_da_pessoa)
        self.assertFalse(pessoa.check_password("senhaEscolhidaPelaGestao"))

    def test_quem_nao_responde_pelo_condominio_nao_define_senha(self):
        """A supervisão vê a tela, mas não redefine senha de ninguém."""
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        vinculo = criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        senha_da_pessoa = pessoa.password

        self.client.logout()
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisao@exemplo.test"
        )
        resposta = self.client.post(
            reverse("cadastros:definir_senha_acesso", args=[vinculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "senha": "outraSenhaBoa2026",
                "confirmacao": "outraSenhaBoa2026",
            },
        )

        self.assertEqual(resposta.status_code, 403)
        pessoa.refresh_from_db()
        self.assertEqual(pessoa.password, senha_da_pessoa)

    def test_a_lista_nao_oferece_redefinir_senha_a_quem_nao_responde(self):
        pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)

        self.client.logout()
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisao@exemplo.test"
        )
        rotulos = [
            acao["label"]
            for item in self.client.get(self.url).context["acessos"]
            for acao in item["acoes"]
        ]

        self.assertNotIn("Redefinir senha", rotulos)

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


class SenhaDefinidaPelaAdministracaoTests(TestCase):
    """A administração técnica define a senha de outra pessoa.

    Existe uma única porta em que alguém escolhe a senha de outra conta: um
    ambiente sem entrega de e-mail não teria como devolver o acesso a quem
    perdeu a senha. Aqui quem entra é a administração técnica — a síndica com
    acesso administrativo —, porque o atributo continua valendo; o que não
    vale é ele sozinho, sem responder pelo condomínio. A senha não aparece na
    tela nem no histórico, e quem protege o valor é o Django.
    """

    SENHA_NOVA = "outraSenhaBoa2026"

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:acessos")
        self.admin = entrar_como(
            self.client, Papel.SINDICA, self.condominio, "admin@exemplo.test"
        )
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        self.vinculo = criar_vinculo(
            self.pessoa, self.condominio, papel=Papel.MORADOR
        )

    def _definir(self, senha=None, confirmacao=None, vinculo=None):
        senha = self.SENHA_NOVA if senha is None else senha
        alvo = vinculo or self.vinculo
        return self.client.post(
            reverse("cadastros:definir_senha_acesso", args=[alvo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "senha": senha,
                "confirmacao": senha if confirmacao is None else confirmacao,
            },
            follow=True,
        )

    # -- O que a ação faz -----------------------------------------------------

    def test_a_administracao_define_a_senha_de_outra_pessoa(self):
        resposta = self._definir()

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Senha atualizada com sucesso.")
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(self.SENHA_NOVA))
        self.assertFalse(self.pessoa.check_password(SENHA))

    def test_a_conta_sem_senha_passa_a_ter_uma(self):
        sem_senha = Usuario(email="nova@exemplo.test", full_name="Pessoa Nova")
        sem_senha.set_unusable_password()
        sem_senha.save()
        vinculo = criar_vinculo(sem_senha, self.condominio, papel=Papel.MORADOR)
        self.assertFalse(sem_senha.has_usable_password())

        self._definir(vinculo=vinculo)

        sem_senha.refresh_from_db()
        self.assertTrue(sem_senha.has_usable_password())
        self.assertTrue(sem_senha.check_password(self.SENHA_NOVA))

    def test_a_pessoa_entra_com_a_senha_definida(self):
        self._definir()

        self.client.logout()
        entrada = self.client.post(
            reverse("accounts:login"),
            {"username": self.pessoa.email, "password": self.SENHA_NOVA},
        )
        self.assertRedirects(entrada, reverse("core:home"))

    # -- O que a ação recusa --------------------------------------------------

    def test_as_regras_de_senha_do_sistema_valem_aqui(self):
        resposta = self._definir(senha="curta")

        self.assertEqual(resposta.status_code, 400)
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(SENHA))

    def test_as_duas_senhas_precisam_ser_iguais(self):
        resposta = self._definir(confirmacao="umaSenhaDiferente2026")

        self.assertEqual(resposta.status_code, 400)
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(SENHA))

    def test_vinculo_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        alheia = criar_pessoa("de-outro@exemplo.test", "Pessoa de Outro")
        vinculo = criar_vinculo(alheia, outro, papel=Papel.MORADOR)

        self.assertEqual(self._definir(vinculo=vinculo).status_code, 404)

    def test_o_acesso_administrativo_nao_substitui_o_vinculo_com_o_condominio(self):
        """Ver a tela não é responder pelo condomínio: a segunda pergunta continua."""
        supervisora = entrar_como(
            self.client,
            Papel.SUPERVISORA,
            self.condominio,
            "supervisora@exemplo.test",
        )
        supervisora.is_staff = True
        supervisora.save(update_fields=["is_staff"])

        self.assertEqual(self._definir().status_code, 403)

    # -- O que nunca aparece --------------------------------------------------

    def test_o_modal_pede_so_a_senha_nova(self):
        resposta = self.client.get(
            self.url,
            {
                "condominio": str(self.condominio.pk),
                "abrir": "senha",
                "item": str(self.vinculo.pk),
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["modal_aberto"], "senha")
        formulario = resposta.context["formulario_senha"]
        self.assertEqual(list(formulario.fields), ["senha", "confirmacao"])
        self.assertContains(resposta, 'id="definir-senha"')
        self.assertContains(resposta, "Confirmar nova senha")

    def test_a_senha_nunca_aparece_na_tela(self):
        resposta = self._definir()

        self.assertNotContains(resposta, self.SENHA_NOVA)
        self.assertNotContains(resposta, "pbkdf2")
        self.assertNotContains(resposta, "has_usable_password")

    def test_o_historico_guarda_o_fato_e_nunca_a_senha(self):
        self._definir()

        self.pessoa.refresh_from_db()
        registro = HistoricoAcessoCondominio.objects.get(
            acesso=self.vinculo, evento=EventoAcesso.SENHA_DEFINIDA_PELA_GESTAO
        )
        self.assertEqual(registro.usuario_executor, self.admin)
        self.assertIsNotNone(registro.data_hora)
        conteudo = f"{registro.dados_anteriores}{registro.dados_novos}"
        self.assertNotIn(self.SENHA_NOVA, conteudo)
        self.assertNotIn(self.pessoa.password, conteudo)
        self.assertNotIn("pbkdf2", conteudo)

    def test_a_acao_aparece_para_quem_administra_contas(self):
        por_email = {
            item["email"]: [acao["label"] for acao in item["acoes"]]
            for item in self.client.get(self.url).context["acessos"]
        }

        self.assertIn(
            "Redefinir senha", por_email["conhecida@exemplo.test"]
        )


class RedefinicaoDeSenhaPelaGestaoTests(TestCase):
    """Quem responde pelo condomínio redefine a senha de quem tem acesso lá.

    É a mesma permissão que cria o acesso: quem responde pelo condomínio
    responde pela senha dele. O acesso administrativo continua podendo, mas
    não é requisito — e, sozinho, não substitui o vínculo com o condomínio.
    """

    SENHA_NOVA = "outraSenhaBoa2026"

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:acessos")
        self.gestao = entrar_como(
            self.client, Papel.ADMINISTRADORA, self.condominio, "gestao@exemplo.test"
        )
        self.pessoa = criar_pessoa("conhecida@exemplo.test", "Pessoa Conhecida")
        self.vinculo = criar_vinculo(
            self.pessoa, self.condominio, papel=Papel.MORADOR
        )

    def _redefinir(self, senha=None, vinculo=None, follow=False):
        senha = self.SENHA_NOVA if senha is None else senha
        alvo = vinculo or self.vinculo
        return self.client.post(
            reverse("cadastros:definir_senha_acesso", args=[alvo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "senha": senha,
                "confirmacao": senha,
            },
            follow=follow,
        )

    # -- Quem redefine --------------------------------------------------------

    def test_a_gestao_do_condominio_redefine_sem_acesso_administrativo(self):
        resposta = self._redefinir(follow=True)

        self.assertFalse(self.gestao.is_staff)
        self.assertContains(resposta, "Senha atualizada com sucesso.")
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(self.SENHA_NOVA))

    def test_o_acesso_administrativo_continua_redefinindo(self):
        self.gestao.is_staff = True
        self.gestao.save(update_fields=["is_staff"])

        self._redefinir()

        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(self.SENHA_NOVA))

    def test_a_acao_aparece_para_quem_responde_pelo_condominio(self):
        rotulos = [
            acao["label"]
            for item in self.client.get(self.url).context["acessos"]
            for acao in item["acoes"]
        ]

        self.assertFalse(self.gestao.is_staff)
        self.assertIn("Redefinir senha", rotulos)

    def test_o_modal_abre_para_quem_responde_pelo_condominio(self):
        resposta = self.client.get(
            self.url,
            {
                "condominio": str(self.condominio.pk),
                "abrir": "senha",
                "item": str(self.vinculo.pk),
            },
        )

        self.assertEqual(resposta.context["modal_aberto"], "senha")
        self.assertContains(resposta, 'id="definir-senha"')

    # -- O que a senha faz ----------------------------------------------------

    def test_a_senha_antiga_deixa_de_funcionar_e_a_nova_vale(self):
        self._redefinir()

        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.check_password(SENHA))
        self.assertTrue(self.pessoa.check_password(self.SENHA_NOVA))

        self.client.logout()
        entrada = self.client.post(
            reverse("accounts:login"),
            {"username": self.pessoa.email, "password": self.SENHA_NOVA},
        )
        self.assertRedirects(entrada, reverse("core:home"))

    def test_a_auditoria_guarda_o_fato_e_nunca_a_senha(self):
        self._redefinir()

        self.pessoa.refresh_from_db()
        registro = HistoricoAcessoCondominio.objects.get(
            acesso=self.vinculo, evento=EventoAcesso.SENHA_DEFINIDA_PELA_GESTAO
        )
        self.assertEqual(registro.usuario_executor, self.gestao)
        self.assertIsNotNone(registro.data_hora)
        conteudo = f"{registro.dados_anteriores}{registro.dados_novos}"
        self.assertNotIn(self.SENHA_NOVA, conteudo)
        self.assertNotIn(self.pessoa.password, conteudo)
        self.assertNotIn("pbkdf2", conteudo)

    # -- O que a ação não alcança ---------------------------------------------

    def test_gestor_de_outro_condominio_nao_alcanca_este_acesso(self):
        outro = criar_condominio("Beta")
        self.client.logout()
        entrar_como(self.client, Papel.ADMINISTRADORA, outro, "outra-gestao@exemplo.test")

        resposta = self._redefinir()

        self.assertEqual(resposta.status_code, 404)
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(SENHA))

    def test_quem_nao_enxerga_o_modulo_nao_descobre_a_acao(self):
        """Sem enxergar os cadastros, a tela nem existe para essa pessoa."""
        self.client.logout()
        entrar_como(
            self.client, Papel.OPERACIONAL, self.condominio, "operacional@exemplo.test"
        )

        resposta = self._redefinir()

        self.assertEqual(resposta.status_code, 404)
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(SENHA))

    def test_a_senha_nao_volta_para_a_tela(self):
        resposta = self._redefinir(follow=True)

        self.assertNotContains(resposta, self.SENHA_NOVA)
        self.assertNotContains(resposta, "pbkdf2")


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
        criar_vinculo(outro, self.condominio, papel=Papel.ADMINISTRADORA)
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

    def test_definir_senha_sem_executor_e_recusada_e_nada_muda(self):
        """Sem autor identificado não há registro — e não há alteração."""
        senha_antes = self.sindica.password

        with self.assertRaises(ErroDeAcesso):
            definir_senha_de_acesso(
                acesso=self.vinculo_gestao,
                senha="outraSenhaBoa2026",
                executor=None,
            )

        self.sindica.refresh_from_db()
        self.assertEqual(self.sindica.password, senha_antes)


class CriacaoDeUsuarioComSenhaTests(TestCase):
    """Criar o usuário e escolher como a primeira senha nasce.

    A tela oferece dois caminhos e a escolha é explícita: a própria pessoa
    definirá a dela no primeiro acesso — o caminho de sempre —, ou quem
    responde pelo condomínio define uma agora, para quando não houver e-mail
    que entregue o endereço. Quem responde pelo condomínio é quem tem a
    permissão de gestão dos cadastros; a supervisão enxerga a tela, mas não
    concede acesso. O segundo caminho não é porta dos fundos: sem a permissão
    os campos nem existem, e o que a tela mandar a mais é ignorado.
    """

    SENHA_INICIAL = "senhaInicialBoa2026"

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:acessos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _adicionar(self, follow=False, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "email": "nova@exemplo.test",
            "nome_completo": "Pessoa Nova",
            "papel": Papel.OPERACIONAL,
        }
        return self.client.post(
            reverse("cadastros:adicionar_acesso"), {**dados, **campos}, follow=follow
        )

    def _nova(self):
        return Usuario.objects.get(email="nova@exemplo.test")

    # -- Caminho A: a gestão define a senha agora ------------------------------

    def test_a_tela_oferece_a_escolha_a_quem_responde_pelo_condominio(self):
        """Quem responde pelo condomínio escolhe como a senha inicial nasce.

        A permissão é do domínio, e não um atributo técnico: a síndica não tem
        acesso administrativo e, ainda assim, a escolha aparece na tela e o que
        ela mandar é reconhecido.
        """
        resposta = self.client.get(self.url)

        self.assertFalse(self.gestao.is_staff)
        self.assertTrue(resposta.context["formulario_acesso"].pode_definir_senha)
        self.assertContains(resposta, "Senha de acesso")
        self.assertContains(resposta, "A pessoa definirá a própria senha")
        self.assertContains(resposta, "Definir senha agora")
        self.assertContains(resposta, "Nova senha")
        self.assertContains(resposta, "Confirmar nova senha")

    def test_a_gestao_define_a_senha_inicial(self):
        resposta = self._adicionar(
            modo_senha=SENHA_DA_GESTAO,
            senha=self.SENHA_INICIAL,
            confirmacao=self.SENHA_INICIAL,
        )

        self.assertEqual(resposta.status_code, 302)
        pessoa = self._nova()
        self.assertTrue(pessoa.has_usable_password())
        self.assertTrue(pessoa.check_password(self.SENHA_INICIAL))
        # Com senha definida, não há endereço de primeiro acesso a entregar.
        self.assertEqual(len(mail.outbox), 0)

    def test_a_pessoa_entra_com_a_senha_definida_pela_gestao(self):
        self._adicionar(
            modo_senha=SENHA_DA_GESTAO,
            senha=self.SENHA_INICIAL,
            confirmacao=self.SENHA_INICIAL,
        )

        self.client.logout()
        entrada = self.client.post(
            reverse("accounts:login"),
            {"username": "nova@exemplo.test", "password": self.SENHA_INICIAL},
        )

        self.assertRedirects(entrada, reverse("core:home"))

    def test_a_senha_definida_fica_registrada_sem_o_valor(self):
        self._adicionar(
            modo_senha=SENHA_DA_GESTAO,
            senha=self.SENHA_INICIAL,
            confirmacao=self.SENHA_INICIAL,
        )

        vinculo = AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario__email="nova@exemplo.test"
        )
        registro = HistoricoAcessoCondominio.objects.get(
            acesso=vinculo, evento=EventoAcesso.SENHA_DEFINIDA_PELA_GESTAO
        )
        self.assertEqual(registro.usuario_executor, self.gestao)
        self.assertIsNotNone(registro.data_hora)
        conteudo = f"{registro.dados_anteriores}{registro.dados_novos}"
        self.assertNotIn(self.SENHA_INICIAL, conteudo)
        self.assertNotIn(self._nova().password, conteudo)
        self.assertNotIn("pbkdf2", conteudo)

    def test_a_senha_nova_nao_aparece_na_tela(self):
        resposta = self._adicionar(
            modo_senha=SENHA_DA_GESTAO,
            senha=self.SENHA_INICIAL,
            confirmacao=self.SENHA_INICIAL,
            follow=True,
        )

        self.assertContains(resposta, "Acesso concedido.")
        self.assertNotContains(resposta, self.SENHA_INICIAL)
        self.assertNotContains(resposta, "pbkdf2")

    # -- O que a ação recusa ---------------------------------------------------

    def test_as_duas_senhas_precisam_ser_iguais(self):
        resposta = self._adicionar(
            modo_senha=SENHA_DA_GESTAO,
            senha=self.SENHA_INICIAL,
            confirmacao="umaSenhaDiferente2026",
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Usuario.objects.filter(email="nova@exemplo.test").exists())

    def test_as_regras_de_senha_do_sistema_valem_aqui(self):
        resposta = self._adicionar(
            modo_senha=SENHA_DA_GESTAO, senha="curta", confirmacao="curta"
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Usuario.objects.filter(email="nova@exemplo.test").exists())

    def test_quem_nao_responde_pelo_condominio_nao_escolhe_senha(self):
        """A supervisão enxerga a tela, mas não concede acesso nem escolhe senha."""
        self.client.logout()
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisao@exemplo.test"
        )

        resposta = self._adicionar(
            modo_senha=SENHA_DA_GESTAO,
            senha=self.SENHA_INICIAL,
            confirmacao=self.SENHA_INICIAL,
        )

        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Usuario.objects.filter(email="nova@exemplo.test").exists())

    # -- Caminho B: a própria pessoa define ------------------------------------

    def test_a_pessoa_define_a_propria_senha_no_primeiro_acesso(self):
        resposta = self._adicionar(modo_senha=SENHA_DA_PROPRIA_PESSOA, follow=True)

        self.assertContains(resposta, "Enviamos as instruções para nova@exemplo.test.")
        self.assertFalse(self._nova().has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("primeiro-acesso", mail.outbox[0].body)

    def test_sem_escolha_declarada_a_senha_continua_sendo_da_pessoa(self):
        """A tela não escolhe por omissão: sem resposta, o caminho é o de sempre."""
        resposta = self._adicionar(follow=True)

        self.assertContains(resposta, "Enviamos as instruções para nova@exemplo.test.")
        self.assertFalse(self._nova().has_usable_password())


class FotoDoUsuarioTests(TestCase):
    """A foto de quem usa o sistema: opcional, conferida e substituível.

    Um retrato é identificação, não acervo. A imagem é conferida pelo conteúdo
    real — o nome e o tipo informado não são fonte de confiança —, fica no
    armazenamento privado e aparece pequena ao lado do nome. Sem foto, a pessoa
    aparece pelas iniciais; e uma falha de armazenamento custa o retrato, nunca
    o acesso.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:acessos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.provider = StorageDeMentira("bucket-fotos")

        patcher = patch(
            "apps.accounts.avatar.obter_provider", return_value=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _adicionar(self, follow=False, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "email": "nova@exemplo.test",
            "nome_completo": "Pessoa Nova",
            "papel": Papel.OPERACIONAL,
        }
        return self.client.post(
            reverse("cadastros:adicionar_acesso"), {**dados, **campos}, follow=follow
        )

    def _editar(self, vinculo, **campos):
        dados = {"condominio": str(self.condominio.pk), "papel": vinculo.papel}
        return self.client.post(
            reverse("cadastros:editar_acesso", args=[vinculo.pk]),
            {**dados, **campos},
        )

    def _nova(self):
        return Usuario.objects.get(email="nova@exemplo.test")

    def _vinculo(self):
        return AcessoUsuarioCondominio.objects.get(
            condominio=self.condominio, usuario__email="nova@exemplo.test"
        )

    # -- Guardar ---------------------------------------------------------------

    def test_cria_usuario_com_foto(self):
        resposta = self._adicionar(foto=png())

        self.assertEqual(resposta.status_code, 302)
        pessoa = self._nova()
        self.assertTrue(pessoa.tem_foto)
        self.assertEqual(pessoa.avatar_storage_provider, self.provider.nome)
        self.assertEqual(pessoa.avatar_storage_bucket, self.provider.bucket)
        self.assertIn(pessoa.avatar_storage_key, self.provider.objetos)

    def test_cria_usuario_sem_foto(self):
        resposta = self._adicionar()

        self.assertEqual(resposta.status_code, 302)
        pessoa = self._nova()
        self.assertFalse(pessoa.tem_foto)
        self.assertEqual(pessoa.avatar_storage_key, "")
        self.assertEqual(self.provider.objetos, {})

    def test_sem_foto_a_lista_mostra_as_iniciais(self):
        self._adicionar()

        resposta = self.client.get(self.url)

        self.assertContains(resposta, 'aria-hidden="true">PN</span>', html=False)
        # A classe aparece também na prévia do modal de adicionar acesso; o que
        # não pode existir é o retrato de verdade na linha da pessoa.
        self.assertNotContains(resposta, 'class="eithra-avatar-foto"', html=False)

    def test_com_foto_a_lista_mostra_o_retrato_ao_lado_do_nome(self):
        self._adicionar(foto=png())

        resposta = self.client.get(self.url)

        self.assertContains(resposta, 'class="eithra-avatar-foto"', html=False)
        self.assertIn(self._nova().avatar_storage_key, resposta.content.decode())

    # -- Substituir e tirar ----------------------------------------------------

    def test_substitui_a_foto_pela_nova(self):
        self._adicionar(foto=jpg())
        anterior = self._nova().avatar_storage_key

        self._editar(self._vinculo(), foto=png())

        pessoa = self._nova()
        self.assertNotEqual(pessoa.avatar_storage_key, anterior)
        self.assertTrue(pessoa.avatar_storage_key.endswith(".png"))
        # O objeto que saiu de circulação não fica perdido no bucket.
        self.assertIn(anterior, self.provider.removidos)
        self.assertIn(pessoa.avatar_storage_key, self.provider.objetos)

    def test_editar_sem_escolher_imagem_mantem_a_foto_atual(self):
        self._adicionar(foto=png())
        chave = self._nova().avatar_storage_key

        resposta = self._editar(self._vinculo())

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self._nova().avatar_storage_key, chave)
        self.assertEqual(self.provider.removidos, [])

    def test_remove_a_foto_sem_apagar_a_pessoa(self):
        self._adicionar(foto=png())
        chave = self._nova().avatar_storage_key

        resposta = self._editar(self._vinculo(), remover_foto="on")

        self.assertEqual(resposta.status_code, 302)
        pessoa = self._nova()
        self.assertFalse(pessoa.tem_foto)
        self.assertEqual(pessoa.avatar_storage_key, "")
        self.assertIn(chave, self.provider.removidos)
        self.assertTrue(
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, usuario=pessoa
            ).exists()
        )

    # -- O que a ação recusa ---------------------------------------------------

    def test_arquivo_que_nao_e_imagem_e_recusado(self):
        arquivo = SimpleUploadedFile(
            "foto.png", b"<html>nao sou imagem</html>", content_type="image/png"
        )

        resposta = self._adicionar(foto=arquivo)

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Usuario.objects.filter(email="nova@exemplo.test").exists())
        self.assertEqual(self.provider.objetos, {})

    @override_settings(EITHRA_AVATAR_MAX_BYTES=10)
    def test_imagem_acima_do_limite_e_recusada(self):
        resposta = self._adicionar(foto=png())

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Usuario.objects.filter(email="nova@exemplo.test").exists())
        self.assertEqual(self.provider.objetos, {})

    # -- Quando o armazenamento não responde -----------------------------------

    def test_falha_no_armazenamento_nao_desfaz_o_acesso(self):
        self.provider.falhar_ao_enviar = True

        resposta = self._adicionar(foto=png(), follow=True)

        self.assertContains(resposta, "não foi possível guardar a foto")
        self.assertTrue(
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, usuario__email="nova@exemplo.test"
            ).exists()
        )
        self.assertFalse(self._nova().tem_foto)

    def test_foto_ilegivel_nao_derruba_a_lista(self):
        self._adicionar(foto=png())

        with patch.object(
            self.provider,
            "url_assinada",
            side_effect=avatar.ErroDeStorage("fora do ar"),
        ):
            resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'aria-hidden="true">PN</span>', html=False)
