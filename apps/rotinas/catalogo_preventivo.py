"""Dados iniciais da biblioteca de atividades preventivas.

Frequências sem cadência confiável ficam sem periodicidade interna; a ativação
exige que a pessoa escolha uma recorrência ou uma única ocorrência.
"""
from apps.core.recorrencia import Periodicidade

from .constants import TipoRotina


def _modelo(
    chave,
    categoria,
    titulo,
    frequencia,
    checklist,
    *,
    tipo=TipoRotina.PREVENTIVA,
    periodicidade_tipo="",
    intervalo=None,
    unidade="",
    equipamentos=(),
    locais=(),
    equivalentes=(),
    observacoes="",
    fornecedor=False,
    validacao=False,
    condicao="",
    sugerir_sempre=False,
):
    return {
        "chave": chave,
        "categoria": categoria,
        "titulo": titulo,
        "periodicidade_sugerida": frequencia,
        "periodicidade_tipo": periodicidade_tipo,
        "periodicidade_intervalo": intervalo,
        "unidade_periodicidade": unidade,
        "tipo": tipo,
        "gatilhos_equipamento": list(equipamentos),
        "gatilhos_local": list(locais),
        "titulos_equivalentes": [titulo, *equivalentes],
        "checklist_sugerido": list(checklist),
        "observacoes": observacoes,
        "exige_fornecedor_especializado": fornecedor,
        "exige_validacao_tecnica": validacao,
        "condicional": bool(condicao),
        "condicao_necessaria": condicao,
        "sugerir_sempre": sugerir_sempre,
    }


M = Periodicidade.MENSAL
S = Periodicidade.SEMANAL
T = TipoRotina.INSPECAO
L = TipoRotina.LIMPEZA
P = TipoRotina.PREVENTIVA
G = TipoRotina.SEGURANCA

MODELOS_INICIAIS = (
    _modelo("elevador-inspecao-mensal", "Elevadores", "Inspecionar elevadores", "Mensal", (
        "Portas", "Nivelamento", "Sensores", "Ruídos anormais", "Iluminação",
        "Interfone e alarme", "Falhas registradas",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("elevador", "elevadores")),
    _modelo("elevador-visita-manutencao", "Elevadores", "Conferir visita da manutenção dos elevadores", "Mensal", (
        "Visita realizada", "Ordem de serviço recebida", "Peças substituídas",
        "Pendências", "Evidência anexada",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("elevador", "elevadores"), fornecedor=True),
    _modelo("gerador-teste-mensal", "Gerador", "Testar grupo gerador", "Mensal", (
        "Partida", "Bateria", "Nível de óleo", "Combustível", "Vazamentos",
        "Ruídos", "Tensão", "Painel",
    ), periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("grupo gerador", "gerador", "geradores")),
    _modelo("gerador-teste-com-carga", "Gerador", "Testar gerador com carga", "Trimestral", (
        "Transferência", "Carga", "Estabilidade", "Retorno à rede", "Alarmes",
    ), periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("grupo gerador", "gerador", "geradores"),
        observacoes="Confirme o procedimento com o plano do fabricante ou fornecedor."),
    _modelo("gerador-revisao-completa", "Gerador", "Revisão completa do gerador", "Anual ou conforme fabricante", (
        "Óleo", "Filtros", "Bateria", "Arrefecimento", "Alternador",
        "Quadro de transferência",
    ), periodicidade_tipo=Periodicidade.ANUAL, intervalo=1, unidade="anos",
        equipamentos=("grupo gerador", "gerador", "geradores"),
        observacoes="A frequência deve seguir o plano do fabricante ou fornecedor.",
        fornecedor=True, validacao=True),
    _modelo("portao-garagem-interno", "Portões e acesso", "Inspecionar portão interno da garagem", "Mensal", (
        "Motor", "Cremalheira", "Sensores", "Fim de curso", "Ruído",
        "Lubrificação", "Segurança",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("portão", "portões", "motor de portão")),
    _modelo("portao-garagem-externo", "Portões e acesso", "Inspecionar portão externo da garagem", "Mensal", (
        "Motor", "Sensores", "Controles", "Estrutura", "Corrosão", "Fechamento",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("portão", "portões", "motor de portão")),
    _modelo("controle-acesso-clausura", "Portões e acesso", "Testar controle de acesso e clausura", "Mensal", (
        "Leitura facial", "Fechaduras", "Portas", "Fontes", "Nobreak", "Registros",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("controle de acesso", "clausura", "leitor facial")),
    _modelo("interfones-teste", "Portões e acesso", "Testar interfones", "Mensal ou trimestral", (
        "Chamada", "Áudio", "Abertura", "Unidades com falha", "Portaria",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("interfone", "interfones", "central de interfone")),
    _modelo("cftv-verificacao", "Portões e acesso", "Verificar CFTV", "Mensal", (
        "Câmeras", "Gravação", "Data e hora", "Imagem noturna", "HD ou NVR",
        "Retenção",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("cftv", "câmera", "câmeras", "nvr", "gravador de vídeo")),
    _modelo("emergencia-iluminacao", "Incêndio e emergência", "Testar iluminação de emergência", "Mensal", (
        "Acendimento", "Autonomia", "Bateria", "Luminárias danificadas",
    ), tipo=G, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("iluminação de emergência", "luminária de emergência")),
    _modelo("incendio-extintores", "Incêndio e emergência", "Inspecionar extintores", "Mensal, inspeção visual", (
        "Validade", "Lacre", "Pressão", "Sinalização", "Acesso",
    ), tipo=G, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("extintor", "extintores")),
    _modelo("incendio-hidrantes", "Incêndio e emergência", "Inspecionar hidrantes e mangueiras", "Mensal, inspeção visual", (
        "Mangueira", "Esguicho", "Registro", "Abrigo", "Acesso", "Vazamento",
    ), tipo=G, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("hidrante", "hidrantes", "mangueira de incêndio")),
    _modelo("incendio-bomba", "Incêndio e emergência", "Testar bomba de incêndio", "Conforme plano técnico; sugestão mensal", (
        "Partida", "Pressão", "Painel", "Alimentação", "Vazamentos",
    ), tipo=G, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("bomba de incêndio", "bomba incêndio"),
        observacoes="Confirme o procedimento e a frequência com o responsável técnico.",
        fornecedor=True, validacao=True),
    _modelo("incendio-porta-corta-fogo", "Incêndio e emergência", "Inspecionar portas corta-fogo", "Mensal", (
        "Fechamento", "Mola", "Barra", "Obstrução", "Integridade",
    ), tipo=G, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("porta corta-fogo", "portas corta-fogo")),
    _modelo("emergencia-sinalizacao", "Incêndio e emergência", "Inspecionar sinalização de emergência", "Trimestral", (
        "Placas", "Rotas", "Visibilidade", "Fixação",
    ), tipo=G, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("sinalização de emergência", "placa de emergência")),
    _modelo("spda-visual", "SPDA / para-raios", "Inspecionar SPDA visualmente", "Semestral", (
        "Captores", "Descidas", "Conexões", "Corrosão", "Danos aparentes",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("spda", "para-raios", "para raios")),
    _modelo("spda-tecnico", "SPDA / para-raios", "Inspeção técnica do SPDA", "Conforme laudo ou responsável técnico", (
        "Continuidade", "Aterramento", "Medições", "Documentação",
    ), tipo=T, equipamentos=("spda", "para-raios", "para raios"),
        fornecedor=True, validacao=True),
    _modelo("eletrica-quadros", "Elétrica", "Inspecionar quadros elétricos", "Trimestral", (
        "Aquecimento", "Ruído", "Cheiro", "Oxidação", "Identificação", "Limpeza",
    ), tipo=T, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("quadro elétrico", "quadros elétricos", "painel elétrico")),
    _modelo("eletrica-termografia", "Elétrica", "Termografia elétrica", "Anual", (
        "Barramentos", "Conexões", "Disjuntores", "Pontos quentes",
    ), tipo=T, periodicidade_tipo=Periodicidade.ANUAL, intervalo=1, unidade="anos",
        equipamentos=("quadro elétrico", "quadros elétricos", "painel elétrico"),
        fornecedor=True, validacao=True),
    _modelo("eletrica-dr-dps", "Elétrica", "Testar DR e DPS", "Semestral ou conforme fabricante", (
        "Funcionamento", "Estado", "Indicação de falha",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("dispositivo dr", "dr", "dps", "quadro elétrico"),
        observacoes="Confirme a frequência indicada pelo fabricante."),
    _modelo("eletrica-nobreak", "Elétrica", "Testar nobreaks", "Trimestral", (
        "Autonomia", "Bateria", "Alarmes", "Carga",
    ), tipo=T, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("nobreak", "nobreaks", "ups")),
    _modelo("hidraulica-bombas-recalque", "Bombas e hidráulica", "Testar bombas de recalque e pressurização", "Mensal", (
        "Alternância", "Pressão", "Vazamentos", "Selo", "Boia", "Painel",
    ), periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("bomba de recalque", "bomba pressurizadora", "pressurizador")),
    _modelo("drenagem-bomba-garagem", "Bombas e hidráulica", "Testar bomba de drenagem da garagem", "Mensal", (
        "Acionamento automático", "Boia", "Vazão", "Obstrução", "Painel",
    ), periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("bomba de drenagem", "bomba de drenagem da garagem"),
        locais=("garagem",)),
    _modelo("drenagem-teste-reforcado", "Bombas e hidráulica", "Testar drenagem antes do período de chuvas", "Antes do período de chuvas", (
        "Bombas", "Ralos", "Grelhas", "Alarmes", "Escoamento",
    ), tipo=T, equipamentos=("bomba de drenagem",), locais=("garagem", "drenagem")),
    _modelo("hidraulica-ralos-grelhas", "Bombas e hidráulica", "Limpar ralos e grelhas", "Mensal ou trimestral", (
        "Sujeira", "Folhas", "Areia", "Fluxo",
    ), tipo=L, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("ralo", "grelha", "drenagem")),
    _modelo("esgoto-tubulacao", "Esgoto, fossas e caixas", "Inspecionar tubulação de esgoto", "Semestral", (
        "Vazamento", "Odor", "Retorno", "Obstrução", "Fissuras",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("tubulação de esgoto",), locais=("esgoto",)),
    _modelo("esgoto-caixas-inspecao", "Esgoto, fossas e caixas", "Inspecionar caixas de inspeção de esgoto", "Semestral", (
        "Nível", "Fluxo", "Tampa", "Vazamento", "Obstrução",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("caixa de inspeção de esgoto", "caixa de esgoto"),
        locais=("caixa de inspeção",)),
    _modelo("esgoto-caixa-gordura", "Esgoto, fossas e caixas", "Limpar caixa de gordura", "Trimestral, ajustar conforme uso", (
        "Acúmulo", "Escoamento", "Odor", "Tampa", "Limpeza registrada",
    ), tipo=L, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("caixa de gordura",), locais=("cozinha",),
        observacoes="Configure a frequência conforme uso e orientação técnica."),
    _modelo("fossa-inspecionar", "Esgoto, fossas e caixas", "Inspecionar fossa séptica", "Semestral", (
        "Nível de lodo", "Retorno", "Odor", "Vazamento", "Tampa", "Saturação",
        "Necessidade de limpeza",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("fossa séptica", "fossa septica"),
        condicao="O condomínio possui fossa séptica."),
    _modelo("fossa-limpar", "Esgoto, fossas e caixas", "Limpar fossa séptica", "Sob demanda ou conforme plano técnico", (
        "Inspeção prévia", "Volume", "Empresa especializada",
        "Destinação do resíduo", "Evidência do serviço",
    ), tipo=L, equipamentos=("fossa séptica",),
        observacoes="Não programe a limpeza automaticamente só pelo calendário.",
        fornecedor=True, validacao=True, condicao="O condomínio possui fossa séptica."),
    _modelo("sumidouro-inspecionar", "Esgoto, fossas e caixas", "Inspecionar sumidouro", "Semestral", (
        "Saturação", "Retorno", "Odor", "Drenagem", "Entorno",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("sumidouro",), condicao="O condomínio possui sumidouro."),
    _modelo("reservatorio-inspecionar", "Reservatórios de água", "Inspecionar reservatórios", "Mensal, inspeção visual", (
        "Tampa", "Vedação", "Vazamento", "Sujeira", "Boia", "Extravasor",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("reservatório", "reservatórios", "caixa d'água",
                      "caixa de água")),
    _modelo("reservatorio-inferior-higienizar", "Reservatórios de água", "Higienizar reservatório inferior", "Semestral", (
        "Esvaziamento", "Limpeza", "Desinfecção", "Tampa", "Boias",
        "Relatório ou evidência",
    ), tipo=L, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("reservatório inferior", "caixa d'água inferior"),
        fornecedor=True),
    _modelo("reservatorio-superior-higienizar", "Reservatórios de água", "Higienizar reservatório superior", "Semestral", (
        "Limpeza", "Desinfecção", "Tampa", "Extravasor", "Boia",
    ), tipo=L, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("reservatório superior", "caixa d'água superior"),
        fornecedor=True),
    _modelo("agua-potabilidade", "Reservatórios de água", "Analisar potabilidade da água", "Conforme plano ou necessidade", (
        "Coleta registrada", "Análise recebida", "Resultado arquivado",
        "Pendências registradas",
    ), tipo=T, equipamentos=("reservatório", "reservatórios"),
        fornecedor=True, validacao=True),
    _modelo("piscina-bomba-filtro", "Piscina", "Inspecionar bomba e filtro da piscina", "Mensal", (
        "Pressão", "Vazamento", "Ruído", "Registros", "Filtro",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("bomba da piscina", "filtro da piscina", "bomba piscina"),
        locais=("piscina", "casa de máquinas da piscina")),
    _modelo("piscina-retrolavar-filtro", "Piscina", "Retrolavar filtro da piscina", "Conforme pressão e uso", (
        "Pressão antes", "Pressão depois", "Fluxo", "Registro",
    ), tipo=L, equipamentos=("filtro da piscina",), locais=("piscina",),
        condicao="O condomínio possui piscina com filtro."),
    _modelo("piscina-inspecao-semanal", "Piscina", "Inspecionar piscina", "Semanal", (
        "Nível", "Ralos", "Rejunte", "Pastilhas", "Bordas", "Escada",
    ), tipo=T, periodicidade_tipo=S, intervalo=1, unidade="semanas",
        locais=("piscina",), condicao="O condomínio possui piscina."),
    _modelo("piscina-impermeabilizacao", "Piscina", "Inspecionar impermeabilização da piscina", "Anual ou por sintoma", (
        "Infiltração", "Fissuras", "Mureta", "Caimentos", "Juntas",
    ), tipo=T, periodicidade_tipo=Periodicidade.ANUAL, intervalo=1, unidade="anos",
        locais=("piscina",), fornecedor=True,
        condicao="O condomínio possui piscina."),
    _modelo("piscina-casa-maquinas", "Piscina", "Inspecionar casa de máquinas da piscina", "Mensal", (
        "Bomba", "Quadro", "Ventilação", "Vazamentos", "Produtos",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("bomba da piscina", "filtro da piscina"),
        locais=("casa de máquinas da piscina",), condicao="O condomínio possui piscina."),
    _modelo("piscina-qualidade-agua", "Piscina", "Verificar qualidade da água da piscina", "Conforme operação", (
        "pH", "Cloro", "Aspecto", "Registro",
    ), tipo=T, locais=("piscina",), condicao="O condomínio possui piscina."),
    _modelo("ar-filtros", "Ar-condicionado", "Limpar filtros do ar-condicionado", "Mensal, conforme uso", (
        "Filtro", "Drenagem", "Odor",
    ), tipo=L, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("ar-condicionado", "ar condicionado", "condensadora",
                      "evaporadora")),
    _modelo("ar-manutencao-preventiva", "Ar-condicionado", "Manutenção preventiva do ar-condicionado", "Trimestral", (
        "Filtros", "Dreno", "Evaporadora", "Condensadora", "Ruído",
    ), periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("ar-condicionado", "ar condicionado", "condensadora",
                      "evaporadora"), fornecedor=True),
    _modelo("academia-inspecao", "Áreas comuns", "Inspecionar academia", "Mensal", (
        "Cabos", "Parafusos", "Estofados", "Bases", "Lubrificação",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("academia", "equipamento de academia"),
        locais=("academia",), condicao="O condomínio possui academia."),
    _modelo("guarda-corpos-corrimaos", "Áreas comuns", "Inspecionar guarda-corpos e corrimãos", "Trimestral", (
        "Fixação", "Corrosão", "Trinca", "Folga",
    ), tipo=T, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("guarda-corpo", "guarda-corpos", "corrimão", "corrimãos")),
    _modelo("pisos-circulacao", "Áreas comuns", "Inspecionar pisos e circulação", "Mensal", (
        "Peças soltas", "Desnível", "Antiderrapância", "Trincas",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("circulação", "corredor", "hall")),
    _modelo("iluminacao-areas-comuns", "Áreas comuns", "Inspecionar iluminação das áreas comuns", "Mensal", (
        "Lâmpadas", "Sensores", "Temporizadores", "Luminárias",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("iluminação", "luminária", "sensor de presença"),
        locais=("área comum", "corredor", "hall")),
    _modelo("tomadas-equipamentos-comuns", "Áreas comuns", "Inspecionar tomadas e equipamentos comuns", "Semestral", (
        "Aquecimento", "Folga", "Dano", "Proteção",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("tomada", "tomadas", "equipamento comum")),
    _modelo("fachada-visual", "Fachada, cobertura e impermeabilização", "Inspecionar fachada visualmente", "Trimestral", (
        "Fissuras", "Revestimentos", "Infiltração", "Corrosão", "Desprendimento",
    ), tipo=T, periodicidade_tipo=M, intervalo=3, unidade="meses",
        locais=("fachada",), condicao="O condomínio possui fachada exposta."),
    _modelo("fachada-tecnica", "Fachada, cobertura e impermeabilização", "Inspeção técnica de fachada", "Conforme plano técnico", (
        "Revestimentos", "Fissuras", "Infiltrações", "Registro técnico",
    ), tipo=T, locais=("fachada",), fornecedor=True, validacao=True,
        condicao="O condomínio possui fachada que requer inspeção técnica."),
    _modelo("esquadrias-selantes", "Fachada, cobertura e impermeabilização", "Inspecionar selantes e esquadrias externas", "Semestral", (
        "Selante", "Fissura", "Entrada de água", "Fixação", "Corrosão",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("esquadria externa",), locais=("fachada",),
        condicao="O condomínio possui esquadrias externas."),
    _modelo("cobertura-inspecionar", "Fachada, cobertura e impermeabilização", "Inspecionar cobertura", "Trimestral", (
        "Impermeabilização", "Ralos", "Fissuras", "Telhados", "Pontos de infiltração",
    ), tipo=T, periodicidade_tipo=M, intervalo=3, unidade="meses",
        locais=("cobertura", "telhado")),
    _modelo("calhas-limpar", "Fachada, cobertura e impermeabilização", "Limpar calhas", "Trimestral ou antes das chuvas", (
        "Folhas", "Areia", "Saída", "Obstrução",
    ), tipo=L, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("calha", "calhas"), locais=("calha", "cobertura"),
        condicao="O condomínio possui calhas."),
    _modelo("juntas-impermeabilizacao", "Fachada, cobertura e impermeabilização", "Inspecionar juntas e impermeabilizações", "Semestral", (
        "Fissuras", "Destacamento", "Umidade", "Falha de selante",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        locais=("junta", "impermeabilização")),
    _modelo("maresia-corrosao", "Maresia / corrosão", "Inspecionar corrosão por maresia", "Mensal ou trimestral, configurável", (
        "Ferragens", "Parafusos", "Suportes", "Portões", "Condensadoras",
        "Quadros", "Estruturas metálicas externas",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        observacoes="Só sugerir após confirmação de exposição à maresia.",
        condicao="O condomínio confirma exposição frequente à maresia."),
    _modelo("maresia-lavagem-componentes", "Maresia / corrosão", "Lavar componentes expostos à maresia", "Conforme material e ambiente", (
        "Exposição salina", "Corrosão", "Lavagem", "Secagem", "Proteção",
    ), tipo=L, observacoes="Defina a frequência conforme os materiais e o ambiente.",
        validacao=True, condicao="O condomínio confirma exposição à maresia."),
    _modelo("maresia-pintura-protecao", "Maresia / corrosão", "Inspecionar pintura e proteção anticorrosiva", "Semestral", (
        "Bolhas", "Ferrugem", "Descascamento", "Metal exposto",
    ), tipo=T, periodicidade_tipo=M, intervalo=6, unidade="meses",
        condicao="O condomínio confirma exposição à maresia."),
    _modelo("pragas-dedetizacao", "Pragas e resíduos", "Dedetização e desinsetização", "Trimestral ou semestral, configurável", (
        "Áreas tratadas", "Produtos", "Empresa", "Certificado", "Observações",
    ), tipo=P, periodicidade_tipo=M, intervalo=3, unidade="meses",
        fornecedor=True, validacao=True),
    _modelo("pragas-desratizacao", "Pragas e resíduos", "Desratização", "Conforme plano da empresa", (
        "Iscas", "Consumo", "Pontos críticos", "Evidências",
    ), tipo=P, fornecedor=True, validacao=True),
    _modelo("lixo-inspecao", "Pragas e resíduos", "Inspecionar casa de lixo", "Semanal ou mensal", (
        "Limpeza", "Drenagem", "Odor", "Portas", "Vetores",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("casa de lixo", "depósito de lixo", "abrigo de resíduos")),
    _modelo("lixo-higienizacao", "Pragas e resíduos", "Higienizar profundamente a casa de lixo", "Mensal", (
        "Lavagem", "Desinfecção", "Ralos", "Paredes",
    ), tipo=L, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("casa de lixo", "depósito de lixo", "abrigo de resíduos")),
    _modelo("lixeiras-inspecao", "Pragas e resíduos", "Inspecionar lixeiras e coletores", "Mensal", (
        "Tampa", "Rachaduras", "Higienização", "Identificação",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        equipamentos=("lixeira", "lixeiras", "coletor de resíduos")),
    _modelo("drenagem-garagem-inspecao", "Drenagem e águas pluviais", "Inspecionar drenagem da garagem", "Mensal", (
        "Ralos", "Grelhas", "Caixas", "Bombas", "Retorno",
    ), tipo=T, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("garagem", "drenagem")),
    _modelo("pluvial-caixas-limpar", "Drenagem e águas pluviais", "Limpar caixas pluviais", "Trimestral ou semestral", (
        "Sedimentos", "Obstrução", "Fluxo",
    ), tipo=L, periodicidade_tipo=M, intervalo=3, unidade="meses",
        equipamentos=("caixa pluvial", "caixa de águas pluviais"),
        locais=("caixa pluvial",)),
    _modelo("pluvial-rede-inspecionar", "Drenagem e águas pluviais", "Inspecionar rede pluvial", "Antes do período de chuvas", (
        "Vazão", "Retorno", "Obstrução", "Vazamento",
    ), tipo=T, equipamentos=("rede pluvial",), locais=("drenagem",)),
    _modelo("pluvial-caixa-areia", "Drenagem e águas pluviais", "Limpar caixa de areia", "Semestral", (
        "Sedimentos", "Obstrução", "Fluxo",
    ), tipo=L, periodicidade_tipo=M, intervalo=6, unidade="meses",
        equipamentos=("caixa de areia",),
        condicao="O condomínio possui caixa de areia na drenagem."),
    _modelo("gas-inspecao", "Gás", "Inspecionar sistema de gás", "Conforme plano técnico", (
        "Odor", "Válvulas", "Reguladores", "Corrosão", "Ventilação",
    ), tipo=T, equipamentos=("gás coletivo", "sistema de gás", "central de gás"),
        fornecedor=True, validacao=True,
        condicao="O condomínio possui sistema de gás coletivo."),
    _modelo("gas-estanqueidade", "Gás", "Testar estanqueidade do gás", "Conforme norma e responsável técnico", (
        "Preparação", "Teste realizado por profissional", "Resultado registrado",
        "Pendências",
    ), tipo=T, equipamentos=("gás coletivo", "sistema de gás", "central de gás"),
        fornecedor=True, validacao=True,
        condicao="O condomínio possui sistema de gás coletivo."),
    _modelo("tecnica-casa-maquinas-limpeza", "Limpeza técnica", "Limpar casa de máquinas", "Mensal", (
        "Poeira", "Óleo", "Objetos", "Acesso", "Ventilação",
    ), tipo=L, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("casa de máquinas",)),
    _modelo("tecnica-areas-limpeza", "Limpeza técnica", "Limpar áreas técnicas", "Mensal", (
        "Organização", "Corrosão", "Obstruções", "Água acumulada",
    ), tipo=L, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("área técnica", "áreas técnicas")),
    _modelo("sazonal-preparar-chuvas", "Preparação sazonal", "Preparar o condomínio para chuvas e ventos fortes", "Sazonal, conforme calendário local", (
        "Bombas", "Ralos", "Portas", "Cobertura", "Fachada", "Gerador",
        "Objetos externos", "Drenagem",
    ), tipo=T, locais=("garagem", "cobertura", "fachada")),
    _modelo("sazonal-pos-temporal", "Preparação sazonal", "Inspecionar o condomínio após temporal", "Sob demanda", (
        "Infiltração", "Fachada", "Cobertura", "Portas", "Equipamentos externos",
        "Drenagem", "Danos aparentes",
    ), tipo=T, locais=("cobertura", "fachada", "garagem")),
    _modelo("seguranca-geral", "Segurança geral", "Inspecionar segurança das áreas comuns", "Mensal", (
        "Risco de queda", "Objetos soltos", "Iluminação", "Acessos", "Barreiras",
        "Pisos", "Corrimãos",
    ), tipo=G, periodicidade_tipo=M, intervalo=1, unidade="meses",
        locais=("área comum", "circulação"), sugerir_sempre=True),
    _modelo("plano-preventivo-revisao", "Revisão do plano", "Revisar plano preventivo do condomínio", "Anual", (
        "Atividades existentes", "Frequências", "Falhas recorrentes",
        "Fornecedores", "Equipamentos novos", "Equipamentos desativados",
        "Atividades nunca executadas", "Adequação das periodicidades",
    ), tipo=T, periodicidade_tipo=Periodicidade.ANUAL, intervalo=1, unidade="anos",
        sugerir_sempre=True),
)
