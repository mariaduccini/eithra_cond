"""Administração de pessoas do condomínio."""
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.core.exceptions import ValidationError

from apps.accounts.models import User


class UserCreationForm(forms.ModelForm):
    """Cria uma pessoa definindo a senha no momento do cadastro."""

    password1 = forms.CharField(
        label="Senha", strip=False, widget=forms.PasswordInput
    )
    password2 = forms.CharField(
        label="Confirmação da senha", strip=False, widget=forms.PasswordInput
    )

    class Meta:
        model = User
        fields = ("email", "full_name")

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise ValidationError("As duas senhas não são iguais.")
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
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
                "fields": ("email", "full_name", "password1", "password2"),
            },
        ),
    )

    filter_horizontal = ("groups", "user_permissions")
