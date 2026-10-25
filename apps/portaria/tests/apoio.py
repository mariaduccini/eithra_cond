from datetime import timedelta

from django.utils import timezone

from apps.comunidade.tests import apoio as comunidade_apoio
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)
from apps.portaria.models import AcessoPortaria, AutorizacaoPortaria


def agora():
    return timezone.localtime()


def criar_autorizacao(condominio, registrado_por, **campos):
    campos.setdefault("tipo_acesso", "visitante")
    campos.setdefault("nome", "Visitante Exemplo")
    campos.setdefault("inicio_previsto", agora() - timedelta(minutes=30))
    campos.setdefault("fim_previsto", agora() + timedelta(hours=1))
    if not campos.get("unidade") and not campos.get("local"):
        campos["unidade"] = comunidade_apoio.criar_unidade(condominio)
    return AutorizacaoPortaria.objects.create(
        condominio=condominio, registrado_por=registrado_por, **campos
    )


def criar_acesso(condominio, usuario, **campos):
    campos.setdefault("tipo_acesso", "visitante")
    campos.setdefault("nome", "Visitante Dentro")
    campos.setdefault("entrada_registrada_por", usuario)
    if not campos.get("unidade") and not campos.get("local"):
        campos["unidade"] = comunidade_apoio.criar_unidade(
            condominio, identificador="102"
        )
    return AcessoPortaria.objects.create(condominio=condominio, **campos)

