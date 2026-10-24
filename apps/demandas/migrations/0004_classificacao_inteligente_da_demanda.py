# A classificação que o EITHRA lê do relato passa a ter onde morar.
#
# São campos novos, todos opcionais e sem padrão que reescreva linha existente:
# as demandas que já estão no banco continuam como estão — vazio ali significa
# "não identificado", e não uma classificação inventada para preencher espaço.
#
# Duas coisas do contexto não viram coluna, de propósito. A área da demanda é
# derivada do tipo do local cadastrado, e a recorrência é uma leitura do
# histórico — persistir qualquer uma delas criaria uma segunda fonte de verdade
# que envelhece sozinha.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("demandas", "0003_demanda_com_equipamento"),
    ]

    operations = [
        migrations.AddField(
            model_name="demanda",
            name="tipo_problema",
            field=models.CharField(
                blank=True,
                choices=[
                    ("vazamento", "Vazamento"),
                    ("infiltracao", "Infiltração"),
                    ("hidraulica", "Falha hidráulica"),
                    ("eletrica", "Falha elétrica"),
                    ("iluminacao", "Iluminação"),
                    ("acesso", "Falha de acesso ou automação"),
                    ("estrutura", "Estrutura ou alvenaria"),
                    ("limpeza", "Limpeza ou conservação"),
                    ("ruido", "Ruído"),
                    ("outro", "Outro"),
                ],
                help_text="A natureza do que está acontecendo, quando identificada.",
                max_length=20,
                null=True,
                verbose_name="Tipo de problema",
            ),
        ),
        migrations.AddField(
            model_name="demanda",
            name="impacto",
            field=models.CharField(
                blank=True,
                choices=[
                    ("unidade", "Uma unidade"),
                    ("area", "Uma área do condomínio"),
                    ("equipamento", "Um equipamento"),
                    ("coletivo", "O condomínio inteiro"),
                ],
                help_text="Até onde a consequência alcança, quando identificado.",
                max_length=20,
                null=True,
                verbose_name="Impacto",
            ),
        ),
        migrations.AddField(
            model_name="demanda",
            name="risco",
            field=models.CharField(
                blank=True,
                choices=[
                    ("sem_risco", "Sem risco identificado"),
                    ("seguranca", "Risco à segurança"),
                    ("incendio", "Risco de incêndio ou explosão"),
                    ("eletrico", "Risco elétrico"),
                    ("estrutural", "Risco estrutural"),
                    ("saude", "Risco à saúde"),
                ],
                help_text="O risco identificado. Vazio é 'não avaliado', que é diferente de 'sem risco'.",
                max_length=20,
                null=True,
                verbose_name="Risco",
            ),
        ),
        migrations.AddField(
            model_name="demanda",
            name="classificacao_sugerida",
            field=models.JSONField(
                blank=True,
                help_text=(
                    "O que o EITHRA propôs ao registrar a demanda, com origem e confiança. "
                    "Nunca é tratado como oficial: existe para comparar sugestão, "
                    "confirmação e correção mais adiante."
                ),
                null=True,
                verbose_name="Classificação sugerida",
            ),
        ),
    ]
