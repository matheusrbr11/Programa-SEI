# Notas de sessão — Programa SEI

> Resumo de uma sessão de revisão/conserto feita com o Claude Code. Guardado aqui porque o histórico da conversa em si fica só na máquina local onde rodou — este arquivo, por estar no compartilhamento de rede, é acessível de qualquer computador.

## Contexto do projeto
**Programa SEI** — automação desktop (customtkinter) do fluxo de **Crédito em Conta** para a SUPCONC/SEFAZ-RJ. `main.py` é a interface; dispara um subprocesso (`processar_cc/`) que automatiza SEI/SIAFE/BB via Selenium. Duas etapas: **Processar Processos** (Etapa 1 — coleta dados + baixa GR, precisa de login SIAFE) e **Responder Processos** (Etapa 2 — anexa/despacha/conclui, só precisa do SEI). Banco SQLite compartilhado com o Programa Hermes, hoje apontando pro `hermes_testes.db` (local ao projeto, temporário — trocar pro caminho de rede do Hermes antes de produção).

Bibliotecas internas usadas: `eop_ui` (`BaseApp`, interface base — login, telas, barra de progresso) e `jupiter` (`SEI`, `Siafe`, automação de fato).

## Cronologia completa

### 1. Revisão inicial
Levantamento de bugs, problemas de performance, segurança e qualidade em `main.py` e todo `processar_cc/`.

### 2. Plano de conserto (12 itens, cada um aprovado individualmente via diff)
1. `stderr=STDOUT` no subprocesso — evitava deadlock por buffer cheio. (`taskkill /IM msedge.exe` foi **mantido** — é workaround intencional pro driver do BB que nunca fechava, confirmado pelo usuário.)
2. `AttributeError` na Etapa 2 sem passar pela Etapa 1 — credenciais do SIAFE inicializadas no `__init__`.
3. Coluna `index_doc` **removida** do banco/código/README (nunca era persistida corretamente; decidiu-se remover em vez de consertar).
4. Atribuição morta `siafe.driver = sei.driver` removida (SIAFE abre driver próprio — confirmado pelo usuário) + limpeza em cascata do parâmetro `sei` não utilizado em `_executar_download_gr_siafe`/`baixar_gr_no_siafe`/`baixar_gr_siafe_por_valor`.
5. `versao_siafe` hardcoded trocado pelo valor calculado.
6. Conexões SQLite fechadas explicitamente (`contextlib.closing`) em `core.py` e `services.py`.
7. `ORDER BY` em `contabilizacoes` — **rejeitado** (processo nunca se repete na tabela).
8. `aguardar_novo_pdf`: sleep fixo → checagem de estabilidade de tamanho do arquivo.
9. Cache de `PASTA_GR` em memória (ver seção própria abaixo).
10. Código de saída dedicado pra "navegador perdido" (depois superado pela renumeração completa — ver seção de códigos).
11. `except: pass` nu → `except Exception: pass` (sem log, a pedido do usuário).
12. CNPJ hardcoded → `config.py` como `CNPJ_ESTADO`.

### 3. Cache de `PASTA_GR`
A pasta de rede era varrida por completo a cada busca de GR/comprovante (até várias vezes por processo). Implementado cache em memória em `utils.py`: lista uma vez por lote, atualiza via `_registrar_arquivo_pasta_gr()` toda vez que o próprio programa grava um arquivo novo. Seguro porque o usuário confirmou que `PASTA_GR` **não** é compartilhada com o Hermes/PRJ — só a Etapa 1 escreve, e é exclusiva deste programa.

### 4. Diagnóstico de performance em `contabilizacoes`
Um `[DIAG]` temporário (depois removido) revelou `buscar_gr_no_banco` levando ~9.8s **por processo**, sempre. Isolado com uma consulta SQL crua (sem nenhum código do projeto) — confirmado: tabela com 9120 linhas, blocos de texto grandes em `observacao`, `LIKE '%...%'` (wildcard no início) força varredura completa via rede a cada chamada. **Não era regressão de nada** — provado revertendo o `closing()` primeiro (não resolveu) e depois isolando com SQL puro. Fix real: mesmo padrão de cache em memória aplicado a `contabilizacoes` (`_listar_contabilizacoes` em `services.py`), lida uma vez por lote. Seguro mesmo sendo escrita pelo módulo PRJ do Hermes: no pior caso (contabilização nova durante o lote) só perde a chance de reaproveitar, cai pro download via SIAFE — não causa ação incorreta.

### 5. Fluxo de login do SIAFE
- Login do SIAFE agora só é pedido **uma vez por sessão** — "Processar Processos" pula direto pra execução se já validado.
- **Bug de roteamento corrigido**: falha de login na Etapa 1 sempre foi tratada como se fosse do SIAFE, mas o único ponto que gerava `motivo: "falha_login"` era a autenticação do **SEI** (confirmado inspecionando o código-fonte da lib `jupiter`: `SEI.logar_sei()` e `Siafe.logar_siafe()` são funções completamente separadas). Falha de SIAFE durante download de GR nem parava o lote — só falhava aquele processo e seguia tentando (e falhando) nos seguintes.
- Corrigido com exceções dedicadas: `ErroLoginSEI` (novo) e `ErroLoginSiafe`. Falha de SIAFE agora **interrompe o lote imediatamente**.

### 6. Bug real: colisão de nomes `self._usuario`/`self._senha`
Usei `self._usuario`/`self._senha` pra guardar a credencial do SIAFE — só que a lib `eop_ui.BaseApp` **já usa exatamente esses nomes internamente** pra qualquer tela de login (inclusive a do SEI!). Resultado: login do SEI "vazava" pra essas variáveis, e o programa pulava a tela do SIAFE usando a credencial errada, logo na primeira execução. Encontrado inspecionando o código-fonte da `BaseApp`. Renomeado pra `self.siafe_usuario`/`self.siafe_senha`.

### 7. Hierarquia de exceções (`processar_cc/core.py`)
Reorganizada a pedido do usuário:
```
Autenticação (param o lote inteiro):
  ErroLoginSEI, ErroLoginSiafe

Base dos erros esperados por processo:
  ErroProcesso

Serviço (não conseguiu completar a interação):
  ErroSEI, ErroSIAFE, ErroBB        (todos ErroProcesso)

Outros:
  ErroExtracao, ErroDownload, ErroValidacao   (todos ErroProcesso)
```
`ErroDownload` ficou **fora** da hierarquia de serviço de propósito — representa "o serviço respondeu bem, mas o documento não existe", eixo diferente de "não consegui nem interagir", e pode acontecer tanto com SIAFE quanto BB.

Convertidos praticamente todos os `return None` silenciosos de `services.py` pra levantar a exceção correta, preservando os loops de fallback (candidatos de comprovante no BB, candidatos de anexo no SEI) via `try/except` nos pontos de chamada. Corrigiu de quebra um bug real: `formatar_despacho_inserido` engolia valor inválido e deixava o processo seguir como se tivesse dado certo — agora levanta `ErroValidacao`.

### 8. Bug do navegador fechado na Etapa 1
Ao migrar as exceções, os novos loops de candidato (`encontrar_dados_em_anexos`, `consultar_conta_judicial`) passaram a **engolir** qualquer exceção, inclusive quando a causa raiz era o navegador fechado manualmente — o loop seguia tentando os próximos candidatos/documentos, cada um falhando, até esgotar e devolver um erro sem ligação com o navegador morto, fazendo o programa achar que era só erro pontual e seguir pro próximo processo (gerando erros em cascata). A Etapa 2 nunca teve isso porque `finalizar_processo` não tem loops de candidato.

Fix: `navegador_perdido()` (a lógica que percorre `__cause__`/`__context__` atrás de um `WebDriverException`) foi movida de `orchestrator.py` pra `utils.py`, tornada pública, e os dois loops de candidato agora checam `navegador_perdido(e)` antes de decidir pular — se for navegador morto, relançam na hora em vez de continuar.

### 9. Códigos de saída — renumeração completa
```
0 = sucesso
1 = sucesso parcial
2 = falha de login do SEI       (motivo: "falha_login_sei")
3 = falha de login do SIAFE     (motivo: "falha_login_siafe")
4 = navegador perdido           (motivo: "navegador_perdido")
5 = erro crítico                (motivo: "erro_critico" + erros de invocação do subprocesso)
```
Implementado via dict `_CODIGOS_SAIDA_FALHA` em `processar_cc/main.py`; interface (`main.py`) e README atualizados.

### 10. Detalhes finais
- `show_login_frame` (método da lib `eop_ui.BaseApp`, não do projeto — não dava pra renomear) ganhou um wrapper `show_sei_login_frame()` no projeto, espelhando o `show_siafe_login_frame()` já existente. As 3 chamadas diretas do login do SEI foram atualizadas pra usar o wrapper.
- No caminho, foi corrigido um erro de sintaxe solto (`elif ret_code == 3:1`) sem relação com as mudanças feitas.
- `contextlib.closing`: aplicado em `core.py` desde o início; revertido temporariamente só em `services.py` durante a investigação do atraso de `contabilizacoes` (não era a causa); reaplicado depois que ficou claro que não tinha motivo pra deixar diferente.

## Estado no fim da sessão
Todo o código compila (`python -m py_compile`). Ainda não testado ao vivo depois das últimas mudanças (bug do navegador fechado, renumeração de códigos, rename do `show_sei_login_frame`) — vale rodar a Etapa 1 de novo e:
1. Confirmar que fechar o navegador no meio do processamento agora interrompe o lote imediatamente (código de saída `4`), sem gerar erros em cascata nos processos seguintes.
2. Confirmar que os códigos de saída novos (0–5) aparecem corretamente na interface.
3. Confirmar que o login do SIAFE continua sendo pedido normalmente na primeira execução.
