"""Backends de autenticação do Eithra."""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailCaseInsensitiveBackend(ModelBackend):
    """Autentica por e-mail ignorando maiúsculas e minúsculas.

    Pessoas digitam o e-mail de formas diferentes; exigir a caixa exata
    criaria barreiras desnecessárias no acesso.

    Contas inativas são recusadas aqui, na fronteira, devolvendo ``None``.
    Consequência intencional: a pessoa vê a mesma mensagem genérica de
    credenciais inválidas, sem confirmar que a conta existe. Isso reduz a
    enumeração de usuários. A mensagem ``inactive`` do formulário fica como
    tradução de segurança, e não como caminho ativo.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        email = username or kwargs.get(UserModel.USERNAME_FIELD)
        if not email or password is None:
            return None

        try:
            user = UserModel._default_manager.get(email__iexact=email.strip())
        except UserModel.DoesNotExist:
            # Executa o hash mesmo sem usuário para reduzir diferença de tempo.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
