"""Remove os resíduos de área e recorrência que nunca pertenceram ao domínio.

A área é leitura do tipo do local e a recorrência é derivada do histórico — as
duas foram decididas como não persistidas, e não existem no model atual. Uma
migration anterior, substituída antes de entrar no repositório, chegou a criar
estas duas colunas no banco de desenvolvimento. Nenhuma linha guarda valor real
nelas: ``area`` está sempre vazia e ``recorrencia`` é nula.

``IF EXISTS`` de propósito: num banco criado do zero a partir destas migrations
as colunas nunca chegam a existir, e a remoção vira um no-op — em vez de um erro
que impediria a suíte de rodar.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("demandas", "0004_classificacao_inteligente_da_demanda"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE demandas_demanda DROP COLUMN IF EXISTS area;",
                "ALTER TABLE demandas_demanda DROP COLUMN IF EXISTS recorrencia;",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
