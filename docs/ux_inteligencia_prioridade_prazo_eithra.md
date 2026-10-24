# EITHRA V3 — UX de Inteligência para Prioridade, Prazo e Sugestões

> Documento de auditoria funcional e de UX. Não autoriza nova integração de IA
> nesta rodada.

## 1. Diretriz base

As telas fornecidas pelo Design para **Demandas** e **Portaria** são o **alvo
visual aprovado**. No caso de Demandas, isso afeta diretamente a etapa
`Informações` do wizard, onde o EITHRA mostra o que conseguiu ler, sugerir ou
derivar com segurança.

Este documento define como essa inteligência deve aparecer para o usuário sem:

- inventar fatos;
- ocultar incerteza;
- transformar hipótese em certeza;
- quebrar a linguagem humana do EITHRA.

## 2. Princípio operacional

O fluxo oficial continua sendo:

**fato → interpretação → contexto → próximo passo**

Na prática:

1. a pessoa descreve o que aconteceu;
2. o sistema lê o que consegue sustentar;
3. o sistema mostra a origem da sugestão;
4. o usuário ajusta o que for necessário;
5. o registro só acontece depois da conferência.

## 3. Estados de informação na interface

Para Demandas, a etapa `Informações sugeridas pelo EITHRA` deve distinguir
claramente:

- **Identificada**: extraída diretamente do relato ou de contexto conhecido.
- **Sugerida**: proposta com base suficiente, mas ainda ajustável.
- **Derivada por regra**: resultado de regra objetiva do domínio.
- **Não identificada**: campo permanece em aberto e deve ser perguntado.

Esses estados podem aparecer em microcopy, legenda curta ou selo discreto, mas
nunca como score técnico.

## 4. Assunto

### O que pode acontecer

- ser identificado do relato;
- ser ajustado pelo usuário;
- permanecer vazio quando o relato não sustenta resumo confiável.

### Regra de UX

O sistema pode preencher `Assunto` a partir da descrição, mas deve permitir
edição antes do registro. O assunto derivado não deve esconder a descrição
original.

## 5. Local

### Fontes válidas

- local citado no próprio relato;
- local conhecido pelo contexto atual;
- escolha do usuário;
- texto livre quando o domínio permitir.

### Regra de UX

Se o local estiver claramente reconhecido, a tela deve mostrar algo como:

- `Identificado no relato`
- `Local conhecido`

Se não estiver, o campo continua aberto. O sistema não deve adivinhar local por
conveniência visual.

## 6. Prioridade

### Fontes válidas

- risco;
- segurança;
- indisponibilidade;
- recorrência;
- equipamento crítico;
- impacto coletivo;
- garantia;
- urgência operacional.

### Regra de UX

Prioridade deve aparecer com explicação humana. Exemplo:

- `Alta`
- `Falha recorrente em equipamento de acesso`

Não exibir:

- score;
- probabilidade numérica;
- peso técnico;
- cadeia interna de decisão.

### Regra de prudência

Sem base suficiente, a prioridade não deve ser afirmada como verdade. Nesse
caso, a interface deve pedir confirmação ou ajuste do usuário.

## 7. Prazo

### Fontes válidas

- regra objetiva derivada do peso da demanda;
- prazo conhecido do contexto;
- decisão explícita do usuário.

### Regra de UX

Prazo deve deixar claro se foi:

- `Derivado por regra`
- `Definido por quem registrou`

Nunca criar data arbitrária silenciosamente. Quando o sistema sugerir um prazo,
o usuário deve conseguir revisá-lo antes do registro.

## 8. Responsável

### Fontes válidas

- responsável conhecido pelo contexto;
- responsável herdado de vínculo válido;
- decisão manual do usuário.

### Regra de UX

Usar a linguagem `Quem acompanha?`. Quando houver sugestão com base real,
explicitar isso de forma simples:

- `Sugerido pelo contexto`
- `Sugerido pelo histórico`

Sem base real, deixar em aberto.

## 9. Situação inicial

A situação inicial de Demandas deve ser exibida na revisão final, mas não como
campo inventado para parecer completo.

Regras:

- usar a situação inicial real suportada pelo domínio;
- não prometer automação de status por evento quando o domínio ainda não a
  sustenta;
- registrar no plano de migração o que depende de evolução do domínio.

## 10. Origem e evidências

A etapa de revisão aprovada pelo Design inclui `Origem e evidências`. Esse bloco
deve mostrar apenas o que o sistema realmente conhece, como:

- registrado por;
- data e hora;
- origem;
- evidências já disponíveis.

Não preencher esse bloco com placeholders fictícios.

## 11. Aplicação em Portaria

Portaria não deve ser tratada como campo de IA, mas também se beneficia de
inteligência contextual controlada. Exemplos válidos:

- reconhecer empresa conhecida no fluxo de prestador;
- sugerir unidade/destino já associado;
- reaproveitar contexto operacional conhecido.

Regras:

- contexto ajuda a preencher;
- porteiro continua confirmando o que ocorreu;
- autorização externa nunca é "deduzida" pelo sistema.

## 12. Microcopy recomendada para a etapa `Informações`

Textos recomendados, alinhados ao Design:

- `Informações sugeridas pelo EITHRA`
- `Identificado no relato`
- `Sugerido pelo histórico`
- `Derivado por regra`
- `Confira e ajuste o que for necessário`
- `Nada foi inventado: o que não teve base continua como pergunta`

## 13. O que a interface não deve fazer

- afirmar certeza onde há apenas hipótese;
- exibir linguagem técnica de motor interno;
- misturar dado confirmado com sugestão sem diferenciação;
- preencher todos os campos só para parecer inteligente;
- esconder do usuário o que foi inferido e o que foi informado.

## 14. Limites atuais do domínio

Com base no estado atual auditado:

- a derivação de Demandas continua determinística e conservadora, e é ela que lê
  o relato primeiro: regras locais respondem por assunto, peso, local,
  equipamento, tipo de problema, impacto, risco e recorrência;
- a rodada de classificação (§6) autorizou **uma** camada semântica, e só para o
  que as regras não alcançam. Ela não decide sozinha: propõe, e a proposta é
  conferida contra o cadastro e o vocabulário do domínio antes de virar sugestão.
  Prioridade e área nunca vêm dela. Sem serviço configurado — ou sem resposta —, o
  resultado é exatamente o das regras, e nenhuma falha aparece para quem usa o
  sistema (ver `apps/core/llm.py` e `apps/demandas/interpretacao.py`);
- cada sugestão guarda valor, origem e confiança; o que foi sugerido fica
  preservado ao lado do que a demanda passou a valer, para que o aprendizado por
  confirmação seja possível mais adiante;
- status ainda não é plenamente derivado por evento real do domínio;
- indicadores e revisões só devem usar cálculos que o backend atual consiga
  sustentar com segurança.

## 15. Critério de aceite

A UX de inteligência estará correta quando:

- o usuário entender o que o sistema soube sozinho;
- o usuário entender o que foi apenas sugerido;
- o usuário puder corrigir antes de registrar;
- nenhuma informação aparecer como certeza sem evidência;
- a explicação for humana e curta.

## 16. Conclusão

O papel da inteligência no EITHRA não é impressionar; é reduzir digitação e
incerteza sem trair a verdade. O Design aprovado para Demandas deve materializar
isso visualmente na etapa `Informações` e na revisão final, sempre com
explicabilidade humana, prudência e rastreabilidade.
