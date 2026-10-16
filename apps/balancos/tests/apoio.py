"""Apoio aos testes de entrada do balanço.

Reúne apenas o que os testes repetem: pessoas, vínculos, um PDF genérico e um
storage de mentira. Nenhum teste unitário fala com o Supabase real, e nenhum
arquivo de balanço de verdade entra aqui.
"""
import hashlib

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.balancos.storage import ErroDeStorage, StorageProvider
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio

# PDF sintético: só a assinatura e o essencial. Não é um balanço real e não
# precisa ser renderizável — o que se verifica aqui é o recebimento.
CONTEUDO_PDF = b"%PDF-1.4\n% documento de teste\n%%EOF\n"
NOME_ARQUIVO = "balanco-do-mes.pdf"
HASH_PDF = hashlib.sha256(CONTEUDO_PDF).hexdigest()


def pdf(nome=NOME_ARQUIVO, conteudo=CONTEUDO_PDF, content_type="application/pdf"):
    """Monta um arquivo enviado, como o navegador entregaria."""
    return SimpleUploadedFile(nome, conteudo, content_type=content_type)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Cria uma pessoa de teste. Nunca usa dados reais."""
    return get_user_model().objects.create_user(
        email=email, password="senha-forte-123", full_name=nome
    )


def criar_vinculo(usuario, condominio, papel=Papel.SINDICA, ativo=True):
    """Liga uma pessoa a um condomínio, com o papel exercido lá."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel, ativo=ativo
    )


class StorageDeMentira(StorageProvider):
    """Storage em memória: guarda o que foi enviado e o que foi removido.

    Existe para que o domínio seja exercitado sem rede. Pode ser programado
    para falhar no envio, que é como se verifica que uma falha de armazenamento
    não vira sucesso.
    """

    nome = "mentira"

    def __init__(self, bucket="bucket-de-teste", falhar_ao_enviar=False):
        super().__init__(bucket=bucket)
        self.objetos: dict[str, bytes] = {}
        self.removidos: list[str] = []
        self.assinaturas: list[tuple[str, int]] = []
        self.falhar_ao_enviar = falhar_ao_enviar

    def enviar(self, *, key, conteudo, content_type):
        if self.falhar_ao_enviar:
            raise ErroDeStorage("Não foi possível falar com o armazenamento.")
        self.objetos[key] = conteudo

    def remover(self, *, key):
        self.removidos.append(key)
        self.objetos.pop(key, None)

    def url_assinada(self, *, key, validade_segundos):
        self.assinaturas.append((key, validade_segundos))
        return f"https://exemplo.invalido/{key}?validade={validade_segundos}"
