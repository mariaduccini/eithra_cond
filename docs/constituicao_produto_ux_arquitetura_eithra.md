# Eithra — Constituição de Produto, UX e Arquitetura

> Documento **normativo permanente**. Vale para **todas** as fases do Eithra, da
> 2E em diante. Toda modelagem, tela, regra de negócio, automação ou integração
> é avaliada contra o que está aqui.
>
> Este documento não descreve o que existe: descreve o que **deve** valer. O que
> existe é auditado contra ele.
>
> A numeração das seções acompanha a especificação original de origem, para que
> a auditoria de cada fase possa citar o mesmo número.

**Ordem de precedência.** O que está nesta Constituição prevalece sobre decisões
pontuais de fase. Quando uma fase precisar contrariá-la, a exceção é registrada
e justificada antes da implementação — nunca depois.

---

## 1. Como usar esta Constituição

Antes de cada fase estrutural, e antes de cada decisão relevante dentro dela,
perguntar:

1. Isto respeita o princípio central (§2)?
2. Isto aumenta a complexidade percebida (§3, §33)?
3. Isto usa a linguagem do §4?
4. Isto pede informação que o Eithra já tem (§5)?
5. Isto cabe nos padrões de ação (§6, §7) e de menu (§8)?
6. Isto duplica um conceito que já tem dono (§9, §10)?
7. Isto persiste o que pode ser derivado (§30)?
8. Isto expõe dado que não precisa ser exposto (§36)?

Quando a resposta a qualquer uma delas for desconfortável, a decisão para e é
revisada.

---

## 2. Princípio central do Eithra

> **O usuário escolhe e confirma.
> O Eithra preenche, relaciona, lembra e enriquece.**

Corolários oficiais:

> **Se o sistema já sabe, não perguntar de novo.**

> **O Eithra não precisa registrar toda conversa.
> Ele identifica o que tem consequência operacional.**

Silêncio é resultado válido: nem toda informação nova exige ação, alerta ou
tela.

---

## 3. Simplicidade percebida

O sistema pode ter arquitetura interna sofisticada. O usuário **não** deve
perceber essa complexidade.

> **complexidade interna ≠ complexidade da experiência**

Várias tabelas internas não justificam vários menus, várias telas ou vários
conceitos apresentados à pessoa.

---

## 4. Linguagem

A linguagem é simples, direta, humana, curta e operacional.

Evitar linguagem de sistema/ERP quando houver expressão comum equivalente.

Evitar na interface, salvo quando realmente necessário: **entidade, workflow,
instância, objeto relacionado, processamento, schema, payload, transação,
inconsistência técnica.**

Preferir:

- "Quem vai cuidar?"
- "Até quando?"
- "Já existe algo parecido."
- "Este contrato vence em 18 dias."
- "Há uma tarefa pendente."
- "O que aconteceu?"
- "Onde aconteceu?"
- "Quem informou?"

---

## 5. Menos cliques

Antes de pedir informação à pessoa, verificar se o Eithra já sabe.

- **Se já souber:** preencher.
- **Se puder inferir com boa confiança e baixo impacto:** sugerir.
- **Se tiver consequência relevante:** pedir confirmação.

Não perguntar novamente: condomínio, fornecedor já relacionado, equipamento já
conhecido, local já conhecido, responsável já herdado, contexto de origem.

---

## 6. Uma ação principal

Cada tela tem **uma** ação principal evidente.

| Contexto | Ação principal |
|---|---|
| Portaria | Chegou / Saiu |
| Morador | Informar problema |
| Supervisão | Atualizar / Concluir |
| Síndica | Decidir / Cobrar |
| Rotina | Executar |
| Financeiro | Revisar / Confirmar |

Ações secundárias ficam discretas.

---

## 7. Ações

Padrão de UX, sem exceção:

- **1 ou 2 ações:** ícones sem borda.
- **3 ou mais:** três pontos + dropdown.
- **Ação crítica:** texto explícito + confirmação quando necessária.

Evitar fileiras de botões.

---

## 8. Menu não é banco de dados

O menu representa tarefas percebidas pela pessoa.

**Não** criar item de menu principal para: tipos, tabelas auxiliares,
históricos, vínculos, alertas, regras internas.

Tipos e catálogos auxiliares são acessados, preferencialmente, **no contexto do
cadastro principal**.

---

## 9. Uma coisa tem um dono

Cada conceito tem uma única fonte de verdade.

- Fornecedor: um cadastro.
- Pessoa: um cadastro.
- Equipamento: um cadastro.
- Documento: um domínio.
- Contrato: um domínio.

Não duplicar o mesmo conceito entre módulos.

---

## 10. Relacionar em vez de duplicar

Preferir relações explícitas. Preservar proveniência.

Exemplo futuro:

```
Sinalização → Demanda → Manutenção → Compra/Custo
```

A entidade anterior continua existindo. Não copiar informação sem necessidade.

---

## 11. Entidades não se transformam

- Sinalização **não vira** Demanda. Pode originar Demanda.
- Demanda **não vira** Manutenção. Pode originar Manutenção.
- Execução de Rotina pode originar uma consequência.

Preservar história e origem.

---

## 12. Memória é requisito central

O Eithra lembra e relaciona o histórico para reduzir esforço humano.

Separar sempre:

- **FATO CONFIRMADO**
- **DECLARADO**
- **INFERIDO**
- **SUGERIDO / APRENDIDO**

**Inferência nunca vira fato silenciosamente.**

---

## 13. Memória de contexto

O sistema pode reaproveitar relações conhecidas:

- equipamento ↔ local;
- fornecedor ↔ especialidade;
- rotina ↔ equipamento;
- contrato ↔ fornecedor;
- classificação financeira confirmada anteriormente.

Objetivo: menos preenchimento.

---

## 14. Memória operacional

O sistema deverá conseguir responder: "isso já aconteceu?", "quantas vezes?",
"qual fornecedor tratou?", "qual manutenção foi executada?", "quanto custou?",
"havia contrato?", "havia garantia?", "voltou a acontecer?", "qual foi o
resultado?".

**Não criar módulo "Memória".** Memória é capacidade transversal.

---

## 15. IA / regras / heurísticas

**Podem:** sugerir, preencher, classificar, detectar duplicidade, identificar
reincidência, recuperar contexto, sugerir relação.

**Não podem, silenciosamente:** aprovar gasto, cancelar contrato, fundir
registros, criar fato definitivo de alto impacto, apagar histórico, alterar dado
original.

---

## 16. Confiança × impacto

Toda automação futura considera **confiança + impacto**.

| Confiança | Impacto | Ação |
|---|---|---|
| Alta | Baixo | pode pré-preencher |
| Baixa | Baixo | sugerir |
| Alta | Alto | confirmar |
| Baixa | Alto | encaminhar para análise humana |

---

## 17. Alertas

Alerta é **condição derivada**.

**Não** criar: menu Alertas, model genérico `Alerta`, cadastro manual de
alertas.

Exemplos: contrato vencendo, garantia vencendo, documento vencendo, preventiva
atrasada, checklist pendente, estoque crítico.

Cada domínio fornece a sua consulta.

---

## 18. Visibilidade ≠ responsabilidade

Separar sempre: quem **executa**, quem **acompanha**, quem **decide**, quem
**visualiza**.

A **síndica** deve conseguir visualizar transversalmente as pendências do
condomínio, inclusive quando Administração, Supervisão ou Operacional são os
responsáveis — para acompanhar e cobrar.

Não transformar a síndica em responsável por tudo.

---

## 19. Governança por exceção

O futuro Painel Hoje organiza nesta ordem:

1. Precisa da sua decisão
2. Está atrasado
3. Está em acompanhamento
4. Informativo

Não colocar todas as pendências no mesmo nível.

---

## 20. Cobrança rastreável

Planejar futuramente a ação **"Cobrar atualização"**.

**Não criar chat.** Registrar: quem cobrou, quando, a pendência, observação
opcional e o histórico.

---

## 21. Portaria

A Portaria é extremamente simples. O porteiro **registra acontecimentos**; não
administra processos.

Tela futura: Esperados agora · Pessoas presentes · Autorizações · Últimas
movimentações.

Ações principais: **Chegou** / **Saiu**.

Não implementar agora.

---

## 22. Câmeras

Preparar arquitetura futura para câmeras, controle de acesso, eventos,
snapshots e leitura de placa.

A câmera será **fonte de evento/evidência**. **Não** será dona da regra de
acesso.

Não implementar agora.

---

## 23. Morador

O morador tem experiência própria e simples.

Futuro: Informar problema · Minhas sinalizações · Acompanhar · Autorizar acesso
· Serviços indicados.

Não expor o sistema administrativo.

---

## 24. Sinalizações

O morador **não** cria Demanda administrativa diretamente.

Futuro:

```
Sinalização → possível duplicidade → confirmação/triagem → Demanda quando necessário
```

Não implementar nesta fase.

---

## 25. Duplicidade

Futuramente verificar: local, categoria, equipamento, janela de tempo,
sinalizações/demandas abertas.

Se semelhante: **"Já existe algo parecido."** Permitir confirmar problema
existente.

**Não fundir silenciosamente.**

---

## 26. Fornecedores indicados a moradores

**REQUISITO OFICIAL DO PRODUTO.**

O cadastro de Fornecedor deverá poder indicar quais fornecedores podem ser
disponibilizados aos moradores.

**Não** criar cadastro paralelo de marketplace. Fornecedor continua sendo um
único cadastro.

Fornecedores indicados aos moradores reutilizam o cadastro de `Fornecedor` e
`EspecialidadeFornecedor`, com dados públicos explicitamente separados dos dados
administrativos.

Suportar futuramente: `indicado_para_moradores`, `ativo_para_moradores`,
`descricao_publica`, `telefone_publico`, `whatsapp_publico`, `site_publico`,
`observacao_publica`, `validado_em`, `validado_por`.

Regras:

- dados internos **nunca** são expostos automaticamente;
- somente campos públicos autorizados aparecem;
- especialidades devem reutilizar `EspecialidadeFornecedor`;
- sem estrelas/reviews nesta primeira versão;
- sem linguagem de garantia do condomínio;
- a contratação continua entre morador e fornecedor.

Preparar `indicados_para_moradores()` e, futuramente,
`indicacao_sem_revalidacao()`.

---

## 27. Reincidência

**Não** criar módulo Reincidência. Derivar do histórico.

Exemplo: equipamento com várias manutenções próximas → sinalizar padrão.

---

## 28. Cobertura

Futuramente o Eithra cruza: contrato, garantia, preventiva, fornecedor, estoque
e histórico.

Antes de um novo gasto, poderá sugerir: **"Este item ainda está em garantia."**

Não implementar inteligência agora.

---

## 29. Financeiro explicável

Futuramente não mostrar apenas categoria + valor. Deve conseguir explicar por
que gastou, com o quê, onde, fornecedor, manutenção, demanda, contrato, projeto
e reincidência.

---

## 30. Não persistir o que pode ser derivado

Evitar campos redundantes como: `vencido`, `vencendo`, `atrasado`,
`garantia_proxima`, `checklist_pendente`.

**Se puder ser calculado confiavelmente: derivar.**

---

## 31. Antes de criar nova tela

Perguntar se o conteúdo deveria ser: **ação, modal, aba, detalhe, filtro ou
contexto dentro de tela existente**.

Só criar tela nova quando representar uma tarefa real.

---

## 32. Antes de criar novo model

Responder:

1. Qual problema resolve?
2. Quem usa?
3. Já existe conceito equivalente?
4. É entidade ou atributo?
5. É relação?
6. Pode ser derivado?
7. Precisa de histórico?
8. Precisa aparecer ao usuário?

Sem justificativa clara: **backlog**.

---

## 33. Orçamento de complexidade

Cada fase tem limite de complexidade percebida.

Pode possuir muitas tabelas internas se tecnicamente necessário. Mas limitar:
menus, telas, conceitos, status, cliques e fluxos.

---

## 34. Dependências

Evitar dependências reversas e circulares.

Preferência: o **novo domínio** pode relacionar o domínio antigo; o **domínio
antigo não precisa conhecer o novo**.

Exemplo aprovado:

```
governanca → rotinas
rotinas NÃO depende de governanca
```

---

## 35. Perfis

| Perfil | Papel |
|---|---|
| ADMIN | estrutura técnica/administrativa |
| SINDICA | governança, visão transversal, decisão e cobrança |
| SUPERVISORA | operação e acompanhamento |
| OPERACIONAL | execução |
| PORTEIRO | Portaria rápida |
| MORADOR | experiência simples e própria |

---

## 36. Privacidade

Aplicar **minimização de dados**.

Não expor sem necessidade: CPF, dados bancários, inadimplência, contrato
interno, documento confidencial, contato privado, observação administrativa,
imagens de acesso, histórico pessoal.

---

## 37. Mobile first

Fluxos de morador, porteiro, operacional e supervisão em campo são desenhados
**primeiro para celular**.

Poucos campos. Poucos cliques. Botões claros. Sem tabela horizontal.

---

## 38. Desktop para gestão

A gestão pode ter tabela, filtros, histórico e análise — ainda assim, **sem
poluição visual**.

---

## 39. Sneat

Manter **Sneat + Bootstrap 5 + Boxicons**.

**Não** criar design system paralelo.

---

## 40. Histórico

Registrar acontecimentos relevantes. **Não** criar feed de tudo.

O histórico responde **"o que aconteceu?"**.

Exemplos: criado, atribuído, iniciado, concluído, cancelado, decisão, cobrança,
alteração de acesso, consequência criada.

---

## 41. Dado original

**Nunca** substituir silenciosamente dado original importado/extraído.

Guardar a decisão humana separadamente quando necessário.

---

## 42. Fases

Toda fase estrutural segue:

```
problema → análise → modelagem → revisão → implementação → testes
→ migration → revisão da migration → migrate DEV → homologação
→ documentação → commit
```

**Não abrir várias fases simultaneamente.**

---

## 43. Testes

Não provar somente status HTTP. Cobrir: regra de negócio, autorização,
cross-condomínio, histórico, idempotência, estados inválidos, operações
repetidas e regressão.

---

## 44. PostgreSQL

PostgreSQL é o banco oficial. **Não** usar SQLite.

Posteriormente poderá existir:

```
PostgreSQL local    → desenvolvimento/testes
PostgreSQL remoto   → integração/homologação
```

---

## 45. Roadmap

| Fase | Tema |
|---|---|
| 2E | Cadastros Estruturantes e Governança |
| 2F | Unidades, Pessoas, Vínculos e Veículos |
| 2G | Portaria Inteligente |
| 2H | Sinalizações e Participação do Morador |
| 2I | Projetos e Obras |
| 2J | Inteligência Financeira |
| 2K | Painel Hoje / Radar Operacional |

---

## 46. Diferenciais planejados

Sinalização inteligente · deduplicação · confirmação comunitária · memória
institucional · radar de risco · reincidência · checagem de contrato/garantia ·
financeiro explicável · cobrança rastreável · fornecedores indicados · portaria
inteligente · integração futura com câmeras · IA por confirmação · síndica com
visão transversal.
