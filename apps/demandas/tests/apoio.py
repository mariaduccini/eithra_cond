"""Apoio aos testes de demandas operacionais.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos, demandas e os
arquivos de prova que o domínio aceita. Nenhum dado real entra aqui — nomes,
e-mails, títulos e mídias são de teste.
"""
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.cadastros.constants import TipoLocal
from apps.cadastros.models import Equipamento, LocalOperacional
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

# A foto é conferida pelo começo real do arquivo, e a régua é a mesma usada nos
# balanços. O apoio de lá já tem um PNG de verdade, um JPEG com cabeçalho
# legítimo, um contêiner WEBP e um storage em memória: repetir isso aqui criaria
# uma segunda versão do mesmo arquivo de teste.
from apps.balancos.tests.apoio import (  # noqa: F401  (reexportado aos testes)
    StorageDeMentira,
    jpg,
    png,
    webp,
)


def criar_condominio(nome="Condomínio de Teste"):
    """Um condomínio de teste, sempre novo."""
    return Condominio.objects.create(nome=nome)


def criar_local(condominio, nome="Garagem", tipo=TipoLocal.OUTRO, **campos):
    """Um local de teste no condomínio informado.

    ``tipo_local`` é o vínculo por condomínio que classifica o lugar; o ``tipo``
    antigo continua aceito porque é ele que o cadastro legado usa.
    """
    return LocalOperacional.objects.create(
        condominio=condominio, nome=nome, tipo=tipo, **campos
    )


def criar_equipamento(condominio, nome="Elevador de serviço", **campos):
    """Um equipamento de teste no condomínio informado."""
    return Equipamento.objects.create(condominio=condominio, nome=nome, **campos)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Uma pessoa de teste. Nunca usa dados reais."""
    return get_user_model().objects.create_user(
        email=email, password="senha-forte-123", full_name=nome
    )


def criar_vinculo(usuario, condominio, papel=Papel.SINDICA, ativo=True):
    """Liga uma pessoa a um condomínio, com o papel exercido lá."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel, ativo=ativo
    )


def montar_condominio_com(papel, email="pessoa@exemplo.test", nome="Pessoa"):
    """Cria condomínio, pessoa e vínculo de uma vez, devolvendo a pessoa.

    Devolve ``(usuario, condominio)`` na ordem em que os testes costumam usar.
    """
    condominio = criar_condominio()
    usuario = criar_pessoa(email, nome)
    criar_vinculo(usuario, condominio, papel=papel)
    return usuario, condominio


# -- Vídeos de evidência ---------------------------------------------------------
# O domínio reconhece o vídeo pela assinatura do contêiner, e é isso que estes
# arquivos exercitam: um MP4 no formato ISO — que traz a marca ``ftyp`` no quinto
# byte — e um WEBM, que é Matroska. Nenhum vídeo real entra nos testes, e nenhum
# deles é reproduzível: o que se confere aqui é o reconhecimento do formato.

NOME_VIDEO = "evidencia.mp4"

CONTEUDO_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 16
CONTEUDO_WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 32


def mp4(nome=NOME_VIDEO, marca=""):
    """Um vídeo MP4 de teste, com o hash que a marca escolher."""
    return SimpleUploadedFile(
        nome, CONTEUDO_MP4 + marca.encode(), content_type="video/mp4"
    )


def webm(nome="evidencia.webm", marca=""):
    """Um vídeo WEBM de teste, com o hash que a marca escolher."""
    return SimpleUploadedFile(
        nome, CONTEUDO_WEBM + marca.encode(), content_type="video/webm"
    )


def arquivo_qualquer(nome="anotacao.txt", conteudo=b"isto nao e uma prova visual"):
    """Um arquivo que não é foto nem vídeo, para exercitar a recusa."""
    return SimpleUploadedFile(nome, conteudo, content_type="text/plain")
