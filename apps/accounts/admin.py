"""Administração de pessoas do condomínio.

O admin do Django é uma bancada de manutenção técnica, e nem aqui a senha de
alguém é escolhida por outra pessoa: o cadastro cria a conta sem senha, e quem
define a sua é sempre o dono dela, pelo fluxo de primeiro acesso. Também não há
tela de troca de senha — a regra vale igual dos dois lados.
"""
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from apps.accounts.models import User


class UserCreationForm(forms.ModelForm):
    """Cria uma pessoa sem senha utilizável.

    A conta nasce como nasce na tela de acessos: sem senha. O operador informa
    quem é a pessoa, e não o que ela vai usar para entrar — quem escolhe a senha
    é ela mesma, pelo endereço de primeiro acesso.
    """

    class Meta:
        model = User
        fields = ("email", "full_name")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_unusable_password()
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    """Edita uma pessoa sem permitir trocar a senha por aqui."""

    password = ReadOnlyPasswordHashField(
        label="Senha",
        help_text="A senha não é exibida. Use o fluxo de troca de senha.",
    )

    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = ("full_name", "email", "is_active")
    list_filter = ("is_active", "is_staff")
    search_fields = ("full_name", "email")
    ordering = ("full_name",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Dados da pessoa", {"fields": ("full_name",)}),
        (
            "Permissões",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Datas", {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )
    readonly_fields = ("last_login", "date_joined", "created_at", "updated_at")

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name"),
            },
        ),
    )

    filter_horizontal = ("groups", "user_permissions")

    def get_urls(self):
        """Sem tela de troca de senha neste admin.

        A regra do Eithra é uma só: ninguém define a senha de outra pessoa. A
        tela que o Django traria aqui faria exatamente isso — o operador
        escolhendo a senha de quem não é ele —, então ela não é registrada. Quem
        precisa de senha nova pede por "esqueci minha senha", ou recebe o
        endereço de primeiro acesso de quem cuida do condomínio.
        """
        return [
            url
            for url in super().get_urls()
            if url.name != "auth_user_password_change"
        ]
