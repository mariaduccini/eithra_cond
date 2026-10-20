"""Armazenamento externo privado — ponto de compatibilidade do domínio de balanços.

A infraestrutura de armazenamento deixou de pertencer a um domínio: na Fase 2E
ela passou a ser compartilhada, e vive em ``apps.core.storage``. Este módulo
continua existindo para que os chamadores de balanços — serviços, detector,
evidências e testes — não precisem mudar.

Ele é apenas uma reexportação: nenhuma regra vive aqui. Quem quiser o contrato
completo ou a implementação deve ler ``apps.core.storage``.

O ``import urllib`` é mantido de propósito: os testes de balanços substituem a
conversa HTTP apontando para ``apps.balancos.storage.urllib.request.urlopen``.
Como ``urllib.request`` é um módulo único no processo, a substituição alcança a
implementação compartilhada do mesmo jeito — mas o nome precisa estar acessível
por este caminho para que a substituição encontre o alvo.
"""
from __future__ import annotations

import urllib.error  # noqa: F401  (alvo de substituição nos testes)
import urllib.parse  # noqa: F401
import urllib.request  # noqa: F401

from apps.core.storage import (  # noqa: F401
    PROVEDOR_PADRAO,
    TEMPO_LIMITE_SEGUNDOS,
    ErroDeStorage,
    StorageProvider,
    SupabaseStorageProvider,
    obter_provider,
)

__all__ = [
    "PROVEDOR_PADRAO",
    "TEMPO_LIMITE_SEGUNDOS",
    "ErroDeStorage",
    "StorageProvider",
    "SupabaseStorageProvider",
    "obter_provider",
]
