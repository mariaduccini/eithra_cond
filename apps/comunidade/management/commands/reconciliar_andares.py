"""Preenche o andar das unidades que já existem, sem criar nem apagar nada.

O prédio da Portaria é desenhado pelos andares reais: a unidade do 9º andar
aparece no 9º andar. Quando o cadastro traz a unidade sem o andar, a planta
perde a verticalidade e as 47 unidades viram uma lista corrida.

O andar não é inventado: ele está escrito no próprio identificador da unidade,
que é como o condomínio a chama. "902" é a unidade 02 do 9º andar; "1600", a
unidade 00 do 16º. Este comando lê isso e preenche apenas o que está vazio.

Ele é conservador de propósito:

* **Não cria e não apaga unidade nenhuma.** Só grava o campo ``andar`` de quem
  está sem ele.
* **Não adivinha o que não é número.** Identificador que não é numérico — "Casa
  03", "A-101", "Loja 02" — fica intocado, porque não há andar a deduzir dele.
* **Não sobrescreve o que já foi informado.** Andar preenchido à mão é da
  gestão, e continua sendo.
* **Nasce em modo de conferência.** Sem ``--aplicar`` o comando só mostra o que
  faria: unidade, andar atual e andar proposto. Quem decide é quem lê.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.comunidade.models import Unidade

# Quantos dígitos finais identificam a unidade dentro do andar. "902" é a
# unidade 02 no 9º; "1600", a unidade 00 no 16º. O que sobra à esquerda é o
# andar.
DIGITOS_DA_UNIDADE = 2

# Menor identificador que ainda tem andar: com menos de três dígitos não há
# andar e unidade — "47" seria um número solto, e não uma unidade de um andar.
MENOR_IDENTIFICADOR_COM_ANDAR = 3


def andar_do_identificador(identificador: str) -> str:
    """O andar que o próprio identificador declara, quando ele declara um.

    Devolve string vazia quando não há o que deduzir — seja porque o
    identificador não é numérico, seja porque é curto demais para conter andar.
    Zeros à esquerda não viram andar: "1001" é o 10º, e não o 010º.
    """
    texto = (identificador or "").strip()
    if not texto.isdigit() or len(texto) < MENOR_IDENTIFICADOR_COM_ANDAR:
        return ""
    return texto[:-DIGITOS_DA_UNIDADE].lstrip("0")


class Command(BaseCommand):
    help = (
        "Preenche o andar das unidades existentes a partir do identificador. "
        "Sem --aplicar, apenas mostra o que mudaria."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava o andar proposto. Sem esta opção, nada é alterado.",
        )
        parser.add_argument(
            "--condominio",
            dest="condominio",
            default="",
            help="Limita a um condomínio, pelo identificador (UUID).",
        )

    def handle(self, *args, **opcoes):
        aplicar = opcoes["aplicar"]
        filtro = opcoes["condominio"].strip()

        unidades = (
            Unidade.objects.select_related("condominio")
            .filter(andar="")
            .order_by("condominio__nome", "identificador")
        )
        if filtro:
            unidades = unidades.filter(condominio_id=filtro)

        antes = Unidade.objects.count()
        propostas = []
        for unidade in unidades:
            proposto = andar_do_identificador(unidade.identificador)
            if proposto:
                propostas.append((unidade, proposto))

        self.stdout.write("unidade | andar atual | andar proposto")
        for unidade, proposto in propostas:
            atual = unidade.andar or "(vazio)"
            self.stdout.write(
                f"{unidade.identificador} | {atual} | {proposto} "
                f"| {unidade.condominio.nome}"
            )

        if not propostas:
            self.stdout.write("Nenhuma unidade sem andar a preencher.")
            return

        self.stdout.write(
            f"\n{len(propostas)} unidade(s) com andar a preencher "
            f"de {antes} no total."
        )

        if not aplicar:
            self.stdout.write(
                "Modo de conferência: nada foi alterado. "
                "Repita com --aplicar para gravar."
            )
            return

        with transaction.atomic():
            for unidade, proposto in propostas:
                unidade.andar = proposto
                unidade.save(update_fields=["andar", "atualizado_em"])

        depois = Unidade.objects.count()
        self.stdout.write(
            f"Andar gravado em {len(propostas)} unidade(s). "
            f"Unidades antes: {antes} · depois: {depois}."
        )
