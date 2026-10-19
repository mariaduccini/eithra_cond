"""Constantes que valem para mais de um domínio do Eithra.

O que mora aqui é vocabulário comum, não regra: o mesmo conjunto de palavras
usado por módulos diferentes. Cada domínio continua com as suas próprias
constantes quando elas só dizem respeito a ele — o que se evita é a mesma
escolha ser declarada duas vezes, com valores que podem divergir depois.
"""
from django.db import models


class Prioridade(models.TextChoices):
    """O quanto uma pendência pesa, na leitura de quem acompanha o condomínio.

    Não há matriz de impacto e urgência: quatro degraus, com ``NORMAL`` como
    padrão, evitam que tudo vire urgente e mantêm a lista utilizável. Demandas e
    manutenções leem o peso com as mesmas palavras — quem compara as duas listas
    está falando do mesmo assunto.
    """

    BAIXA = "baixa", "Baixa"
    NORMAL = "normal", "Normal"
    ALTA = "alta", "Alta"
    URGENTE = "urgente", "Urgente"
