# A demanda passa a poder apontar o equipamento de que ela trata.
#
# O campo é opcional e não tem padrão: as demandas que já existem continuam sem
# equipamento — vazio ali é "não identificado", e não um aparelho escolhido para
# preencher espaço. O índice acompanha a pergunta que a leitura faz ao procurar
# repetição no mesmo equipamento.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cadastros", "0002_tipos_e_fornecedor_para_moradores"),
        ("demandas", "0002_demanda_local"),
    ]

    operations = [
        migrations.AddField(
            model_name="demanda",
            name="equipamento",
            field=models.ForeignKey(
                blank=True,
                help_text="O equipamento envolvido, quando a demanda é sobre um.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="demandas",
                to="cadastros.equipamento",
                verbose_name="Equipamento",
            ),
        ),
        migrations.AddIndex(
            model_name="demanda",
            index=models.Index(
                fields=["condominio", "equipamento"], name="demanda_cond_equip_idx"
            ),
        ),
    ]
