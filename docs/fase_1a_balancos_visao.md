# Fase 1A — Balanços Mensais: Visão

> Documento de **modelagem**. Nada aqui foi implementado. Não há migration, model,
> view, template, parser, OCR, IA, storage ou publicação nesta fase.

## 1. Por que Balanços vêm primeiro

Toda gestão condominial recebe, todo mês, um documento financeiro da administradora.
Esse documento:

- quase nunca é lido por inteiro;
- raramente é comparado com o mês anterior;
- costuma ficar guardado sem gerar consequência;
- é a principal fonte de verdade sobre o dinheiro do prédio.

O primeiro ganho real do Eithra V3 não é "ter um financeiro". É **transformar um PDF
mensal em compreensão, revisão e transparência**, com pouco esforço humano.

Fluxo de valor:

```
Importar balanço
  → compreender
  → revisar (por exceção)
  → validar
  → acompanhar
  → apresentar aos moradores
```

## 2. Pergunta central que rege o domínio

> "Entrou uma informação nova. Isso muda alguma coisa que merece atenção?"

Consequências diretas:

- **Silêncio é resultado válido.** Se o balanço fecha e nada é incomum, o sistema
  diz "pronto para validação" e não inventa alerta.
- **Consequência antes de dado.** O sistema não mostra um número; mostra o que
  aquele número significa para a gestão.
- **Exceção, não varredura.** A gestão nunca revisa 175 páginas se a máquina já
  tem confiança.

## 3. Escopo

### Dentro do escopo (domínio alvo)

- Importar o PDF mensal da administração.
- Identificar o documento (que condomínio, que competência, que tipo de formato).
- Extrair dados financeiros de gestão.
- Normalizar valores, datas, categorias, fornecedores e competência.
- Reconciliar matematicamente (totais × lançamentos).
- Calcular confiança e apontar inconsistências.
- Apresentar prévia.
- Permitir correção humana.
- Validar e manter histórico mensal versionado.
- Preservar o documento original.
- Preparar uma visão segura e simplificada para moradores.

### Fora do escopo (não é ERP contábil)

- contas a pagar e a receber;
- conciliação bancária completa;
- cobrança, boleto, emissão fiscal;
- fluxo contábil formal;
- orçamento anual;
- gestão de inadimplência individual;
- portal do morador e publicação real.

O Eithra **não será** um ERP contábil. Ele interpreta o que a administradora já
produz e transforma em compreensão e transparência.

## 4. Princípios do domínio

1. **Separação de estados da informação.** Dado do documento ≠ dado extraído ≠
   inferência ≠ dado validado. Cada um tem seu lugar e sua rastreabilidade.
2. **Provenance obrigatória.** Todo dado relevante sabe de onde veio.
3. **Nunca inventar.** Campo ausente é ausente. Não se preenche lacuna com
   suposição.
4. **Inferência nunca vira fato sozinha.** Toda inferência é rotulada e precisa de
   confirmação humana quando tem consequência.
5. **Determinístico primeiro.** Regra e cálculo antes de IA. IA só onde o
   determinístico não resolve.
6. **Controle editorial humano.** Publicar nunca é automático. A gestão decide.
7. **Privacidade por padrão.** O que é interno permanece interno.
8. **Simplicidade na tela, profundidade no sistema.** A complexidade fica no
   backend; a interface mostra o essencial.
9. **Contexto de prédio é o condomínio.** Nenhuma estrutura predial nova é criada
   por antecipação (ver `fase_1a_balancos_modelagem_proposta.md`).

## 5. Fundação preservada

Esta fase **não altera**:

- `accounts.User`;
- `Condominio`;
- `AcessoUsuarioCondominio`;
- autorização centralizada (`apps/condominios/acesso.py`);
- PostgreSQL DEV;
- settings;
- Sneat, Bootstrap 5, Boxicons e a identidade visual Eithra;
- Git e migrations existentes.

Não se recoloca papel global. Não se altera a cardinalidade `User ↔ Condominio`.
Nenhuma alteração de autenticação/autorização é feita sem bloqueio real
identificado.

## 6. Definição de sucesso da Fase 1A

A fase é bem-sucedida quando existe documentação suficiente para que a
implementação (Fase 1B) não precise tomar decisões de arquitetura no calor do
código. Ou seja:

- o contrato interno de dados é independente do layout de qualquer fornecedor;
- a decisão de persistência dos lançamentos está justificada;
- o tratamento de versão, duplicidade e substituição está definido;
- a fronteira de privacidade está explícita;
- as decisões que exigem aprovação humana estão listadas.

## 7. Glossário de estados da informação

| Estado | Significado | Persistência |
| --- | --- | --- |
| **Dado do documento** | Conteúdo literal do PDF original | Só no arquivo original |
| **Dado extraído** | O que o sistema conseguiu ler e estruturar | Guardado como extração |
| **Inferência** | Interpretação possível, não confirmada | Guardada, rotulada |
| **Evidência** | Dado que sustenta ou refuta uma hipótese | Guardada, rotulada |
| **Dado validado** | Valor confirmado por pessoa | Guardado como oficial |
| **Pendência/Exceção** | Ponto que exige olhar humano | Guardada e exibida |

## 8. Glossário de termos do usuário

- **Balanço mensal** — o documento financeiro de um mês de um condomínio.
- **Competência** — o mês a que o balanço se refere.
- **Versão** — uma leitura do mesmo mês; pode ser corrigida e substituída.
- **Pendência** — algo que merece atenção antes de validar.
- **Resumo para moradores** — versão simplificada e aprovada pela gestão.
