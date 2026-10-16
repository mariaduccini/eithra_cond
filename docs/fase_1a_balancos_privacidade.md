# Fase 1A — Balanços Mensais: Privacidade

> Fronteira entre o que é interno e o que pode chegar ao morador. Nada é publicado
> nesta fase.

## 1. Princípio

O balanço contém, potencialmente, dados que **não devem** chegar ao morador.
A projeção para o morador é **por padrão restritiva** e **fail-closed**: se uma
restrição não puder ser aplicada tecnicamente, o dado não é publicado.

Não se copia o balanço inteiro para o morador. Produz-se uma **projeção**.

## 2. Classificação conceitual

| Classe | Definição | Destino |
| --- | --- | --- |
| `GESTAO` | Necessário para gerir | Interno |
| `PUBLICAVEL` | Pode ir ao morador após revisão editorial | Projeção pública |
| `SENSIVEL` | Só com tratamento/agregação explícita | Interno, agregado se publicável |
| `NAO_PUBLICAVEL` | Nunca vai ao morador | Interno |

## 3. Classificação por informação

| Informação | Classe | Observação |
| --- | --- | --- |
| Competência | `PUBLICAVEL` | — |
| Total de receitas | `PUBLICAVEL` | — |
| Total de despesas | `PUBLICAVEL` | — |
| Saldo do mês | `PUBLICAVEL` | — |
| Principais categorias | `PUBLICAVEL` | Visão resumida |
| Principais realizações | `PUBLICAVEL` | Texto aprovado pela gestão |
| Comparação simples com mês anterior | `PUBLICAVEL` | Quando útil |
| Despesas detalhadas linha a linha | `GESTAO` | Não vão ao morador |
| Saldos bancários por conta | `SENSIVEL` | Interno |
| Dados bancários / número de conta | `NAO_PUBLICAVEL` | — |
| Contatos de fornecedor | `NAO_PUBLICAVEL` | — |
| CNPJ/CPF | `NAO_PUBLICAVEL` | Salvo necessidade legal explícita e aprovada |
| Recibos individuais | `NAO_PUBLICAVEL` | — |
| Documentos fiscais completos | `NAO_PUBLICAVEL` | — |
| Dados de unidade | `NAO_PUBLICAVEL` | — |
| Inadimplência individual | `NAO_PUBLICAVEL` | Só versão **agregada** |
| Inadimplência agregada (percentual) | `PUBLICAVEL` | Se aprovado |
| Reconciliações internas | `NAO_PUBLICAVEL` | — |
| Inconsistências técnicas | `NAO_PUBLICAVEL` | — |
| Confidence interno | `NAO_PUBLICAVEL` | Nunca exibir score |
| Provenance | `GESTAO` | Rastreabilidade interna |
| PDF original | `GESTAO` | Ver §5 |
| Observações de gestão | `GESTAO` | — |

## 4. Contrato de projeção para o morador

O morador pode receber **somente**:

- competência;
- total de receitas;
- total de despesas;
- saldo;
- principais categorias;
- principais realizações;
- resumo textual **aprovado**;
- comparação simples com o mês anterior, quando útil.

Regras da projeção:

- não mostrar estrutura contábil complexa;
- não publicar automaticamente;
- toda projeção passa por revisão editorial da gestão;
- campos `SENSIVEL`/`NAO_PUBLICAVEL` **não** entram por padrão;
- se houver qualquer dúvida de classificação, o campo **não** é incluído.

## 5. Documento original (PDF)

- armazenado em **storage externo privado** (nunca no filesystem da Vercel);
- nunca acessível por URL pública direta;
- acesso somente via view autenticada que valida o vínculo com o condomínio e
  emite **signed URL** temporária;
- a disponibilização do PDF aos moradores **não** é automática — é decisão
  editorial explícita (futuro).

## 6. Regras duras

1. Nenhuma visualização de balanço de outro condomínio é possível.
2. Autorização no backend, centralizada; a interface não é controle de acesso.
3. Registro não autorizado responde **404** (a existência não é revelada).
4. Fail-closed: sem confirmação de autorização, acesso negado.
5. Nenhum dado `SENSIVEL`/`NAO_PUBLICAVEL` chega à projeção do morador sem
   tratamento explícito e aprovação.
6. Auditoria preservada para toda alteração de classificação e publicação.
