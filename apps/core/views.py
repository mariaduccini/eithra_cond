"""Views do núcleo."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Ponto de entrada do sistema depois do acesso."""
    return render(request, "pages/home.html")
