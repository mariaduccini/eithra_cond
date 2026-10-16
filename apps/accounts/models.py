"""Modelos do app de contas."""
import uuid

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Cria usuários usando o e-mail como identificador."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusuário precisa de is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusuário precisa de is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Pessoa que acessa o Eithra.

    Guarda apenas identidade e autenticação. O que a pessoa faz — e em qual
    condomínio — pertence ao vínculo, em apps.condominios.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("e-mail", unique=True)
    full_name = models.CharField("nome completo", max_length=150)
    is_active = models.BooleanField("ativo", default=True)
    is_staff = models.BooleanField("acesso administrativo", default=False)
    date_joined = models.DateTimeField("entrou em", default=timezone.now)
    created_at = models.DateTimeField("criado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return self.full_name or self.email

    def save(self, *args, **kwargs):
        # Mantém o e-mail em minúsculas para evitar duplicidade por caixa.
        self.email = self.email.lower().strip()
        super().save(*args, **kwargs)

    @property
    def short_name(self) -> str:
        """Primeiro nome, usado em saudações."""
        return self.full_name.split(" ")[0] if self.full_name else self.email

    @property
    def initials(self) -> str:
        """Iniciais para avatar textual."""
        parts = [part for part in self.full_name.split(" ") if part]
        if not parts:
            return self.email[:2].upper()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()
