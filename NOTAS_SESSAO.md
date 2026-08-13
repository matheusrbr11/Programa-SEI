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

## Estado no fim da sessão anterior
Todo o código compilava (`python -m py_compile`). Pendente de teste ao vivo: bug do navegador fechado, renumeração de códigos, rename do `show_sei_login_frame`.

---

## Sessão 2 — Etapa 1 em duas sub-fases (lote de SIAFE)

### Contexto / motivação
Usuário percebeu que a Etapa 1 abria e fechava o navegador do SIAFE-Rio **uma vez por processo** (login completo incluso) sempre que faltava GR — desperdício grande quando vários processos do lote precisam de GR. Proposta: separar em duas sub-fases.

### 1. Reestruturação (`processar_cc/orchestrator.py`, `processar_cc/services.py`)
- **Sub-fase A** (varredura do SEI): `coletar_dados_processo` não acessa mais o SIAFE. Quando a GR não é encontrada localmente (banco `contabilizacoes` ou `PASTA_GR`), grava `status="aguardando_gr"` em vez de baixar na hora. BB continua sendo consultado de forma síncrona nessa sub-fase (decisão do usuário — é a única forma de resolver a conta quando falta o Comprovante já anexado).
- **Sub-fase B** (`_baixar_gr_pendentes_em_lote` + `_agrupar_pendentes_gr`, novo): busca todos os `aguardando_gr`, agrupa por `(versao_siafe, ano_doc)` — chave obrigatória porque o *ano é fixado no momento do login* do SIAFE (`logar_siafe`) e não pode ser trocado depois. Um único driver do Edge é aberto para toda a sub-fase B; a troca de grupo usa **aba nova** (`abrir_nova_aba`/`fechar_aba`, expostos por `automaweb.Navegador` mas não usados antes neste projeto) em vez de reiniciar o navegador — só o login se repete por grupo.
- `_executar_download_gr_siafe` (antiga, "login + 1 consulta + fecha driver") foi dividida em `abrir_sessao_siafe`/`fechar_sessao_siafe` (login/logout de uma sessão) + `baixar_gr_no_siafe`/`baixar_gr_siafe_por_valor` (agora recebem uma sessão `Siafe` já logada em vez de abrir a própria).
- Retomada: se a Etapa 1 for reexecutada após uma interrupção na sub-fase B, processos já `aguardando_gr` **não são remapeados** de novo no SEI (checagem adicionada no loop da sub-fase A).
- Barra de progresso (`__PROGRESSO__`) ajustada para a sub-fase B continuar a contagem de onde a sub-fase A parou, sem estourar o total.
- README atualizado (seção "Etapas do processamento") para descrever as duas sub-fases.

### 2. Bug real encontrado no primeiro teste ao vivo
Log real (6 processos, todos com GR pendente, mesmo grupo versão/ano): a 1ª GR baixou com sucesso; a 2ª falhou com `invalid session id: session deleted...`, e as 4 seguintes falharam em cascata, todas viradas incorretamente para `erro_coleta` no banco, sem o lote parar.

**Causa raiz (diagnosticada pelo usuário):** o botão de filtro do SIAFE (`xpaths_consulta.btn_filtro`) é um **toggle**. Na 1ª consulta do grupo ele abre o painel de filtro; como o painel **permanece aberto** entre consultas na mesma sessão (confirmado pelo usuário: mesmo após uma consulta que retorna "não encontrado", que passa por `_voltar()`), clicar de novo nesse botão na 2ª consulta **fecha** o painel em vez de reabri-lo — quebrando as interações seguintes (`selecionar_texto`/`digitar` em campos agora fora do DOM interativo) e eventualmente derrubando a sessão do driver.

**Problema secundário, também corrigido:** `consultar_GR_numDoc`/`consultar_GR_valor` (lib `jupiter`) capturam `InvalidSessionIdException` **internamente** e só retornam `False`/`None` — a exceção original nunca chega ao código do Programa SEI. `navegador_perdido()` (que só percorre `__cause__`/`__context__` de uma exceção já levantada) não tinha como detectar isso, então "navegador morto" era tratado como "GR não encontrada" e o lote seguia adiante pelos itens restantes.

### 3. Correções aplicadas
- **`processar_cc/utils.py`**: nova função `sessao_siafe_viva(siafe)` — checa ativamente `siafe.driver.window_handles` (chamada real ao driver) para detectar sessão morta mesmo quando a lib engoliu a exceção. Usada em `orchestrator.py` antes de cada consulta do lote (aborta sem tocar nos processos restantes) e depois de qualquer falha (reforço), combinada com `navegador_perdido(e)`.
- **Parâmetro `primeira_consulta`**: adicionado em `baixar_gr_no_siafe`/`baixar_gr_siafe_por_valor` (`services.py`) e propagado por `orchestrator.py` (`True` só no índice 0 de cada grupo). Requer o mesmo parâmetro em `Siafe.consultar_GR_numDoc`/`consultar_GR_valor` (lib `jupiter`, compartilhada com o Hermes) — **a pedido do usuário, essa edição na lib não foi feita pelo Claude**; o texto exato das duas edições foi passado no chat para o usuário aplicar manualmente em `siafelibrary.py` (pula a navegação de menu + o clique em `btn_filtro` quando `primeira_consulta=False`, mantendo o comportamento padrão para chamadas isoladas fora do Programa SEI).

### Estado no fim da sessão 2
Código do Programa SEI compila e já chama `primeira_consulta=...`, mas **isso vai quebrar com `TypeError` até a edição em `siafelibrary.py` ser aplicada manualmente pelo usuário** (parâmetro ainda não existe na lib instalada). Não testado ao vivo depois da correção do bug do toggle de filtro.

---

## Sessão 3 — Bateria de testes ao vivo da sub-fase B (vários bugs reais encontrados e corrigidos)

Usuário já tinha aplicado a edição em `siafelibrary.py` (parâmetro `primeira_consulta` existe e funciona). Cada teste ao vivo revelou um bug novo; todos corrigidos incrementalmente no mesmo `processar_cc`, sem mexer mais na lib `jupiter` além do que o usuário já tinha feito por fora.

### 1. `aguardando_gr` sendo gravado como `erro_coleta` (regra de negócio quebrada)
Qualquer falha pontual na sub-fase B (timeout de UI, GR não encontrada) chamava `_registrar_erro_coleta`, que grava `status="erro_coleta"` — mas esse status é permanente: nada no fluxo volta a reprocessar um `erro_coleta`. Um processo com GR simplesmente lenta pra carregar ficava "morto" no banco pra sempre, mesmo a causa raiz sendo passageira.

**Regra corrigida (não-negociável, a pedido do usuário):** a sub-fase B **nunca** grava `erro_coleta`. Em caso de falha pontual, o registro permanece `aguardando_gr` (o código não toca no banco) — só conta em `estatisticas["erros"]`/`erros_detalhe`. A próxima execução da Etapa 1 automaticamente tenta essas GRs de novo (sub-fase A já pula remapeamento de `aguardando_gr`, sub-fase B sempre busca todos os pendentes atuais).

### 2. `_agrupar_pendentes_gr` recalculava o modo de busca do zero (dessincronia com a sub-fase A)
A função original re-consultava `contabilizacoes` (`buscar_gr_no_banco`) pra decidir se cada processo pendente deveria ser buscado por número de documento ou por valor — em vez de usar o que a sub-fase A já tinha decidido e persistido (`num_doc`). Se uma contabilização nova aparecesse entre as duas sub-fases, a sub-fase B podia escolher "por documento" pra um processo que a sub-fase A classificou (e salvou) como "por valor", abrindo a tela errada no SIAFE.

**Corrigido:** `_agrupar_pendentes_gr` usa exclusivamente `reg["num_doc"]` (já no banco). O loop de download em `_baixar_gr_pendentes_em_lote` monta o dict `registro_gr` localmente a partir de `num_doc`/`valor_pesquisa` já persistidos, sem re-consultar nada.

### 3. Timeout de ~20s propagando em cascata quando a consulta abria a tela errada
Com o bug acima, uma consulta abrindo o modo errado disparava vários `TimeoutException` em sequência (`tempo_wait=20` por chamada da lib), somando 1-2+ minutos "parado" por erro. Decisão: **não mexer no timeout** — resolver a causa raiz (item 2) já eliminava o sintoma. Confirmado nos testes seguintes.

### 4. Retentativa automática — tentada e depois removida
Antes de identificar a causa raiz de #2/#3, foi implementada uma retentativa (1x) quando a consulta retornava vazio. Descoberto um problema real nela: a retentativa rodava **dentro do mesmo `try`**, e como a lib `Siafe.consultar_GR_*` engole `InvalidSessionIdException` internamente (só retorna `False`), uma sessão morta durante a retentativa nunca chegava ao `except` do orchestrator — só o `ErroDownload` levantado depois (`caminho_gr is None`) era capturado, dependendo de uma checagem tardia de `sessao_siafe_viva()` pra detectar o navegador morto. Como o item 1 (nunca gravar `erro_coleta`) já torna uma falha pontual inofensiva (só espera o próximo ciclo), a retentativa foi **removida** — reduz a superfície de mascarar sessão morta sem trazer benefício real.

### 5. Grupo travando ao trocar de modo de busca no meio da mesma sessão
Mesmo com #2 corrigido, um grupo `(versao_siafe, ano_doc)` podia intercalar itens "por documento" e "por valor" (ordem herdada da sub-fase A). O painel de filtro do SIAFE guarda a propriedade selecionada (`'Número'` vs `'Valor'`) entre consultas na mesma sessão. Descoberto que a lib `siafelibrary.py` (já editada pelo usuário) **reconfigura essa propriedade em toda chamada**, independente de `primeira_consulta` — então esse não era mais o problema técnico, mas por clareza/previsibilidade `_agrupar_pendentes_gr` passou a ordenar cada grupo em blocos (todos os "por documento" primeiro, depois todos os "por valor"), preservando a ordem relativa original dentro de cada bloco. `primeira_consulta` continua significando só "1º item do grupo" (não força `True` na troca de modo — o usuário pediu explicitamente pra não fazer isso, já que a lib já cobre o caso).

### 6. Exceção não tratada ao trocar de grupo — processo inteiro derrubado
`abrir_sessao_siafe` só tinha `except ErroLoginSiafe`. Quando a sessão morria entre o último item de um grupo e o login do próximo (ex.: navegador fechado), a exceção (`InvalidSessionIdException` etc.) não era `ErroLoginSiafe` e propagava sem tratamento, derrubando o processo inteiro com traceback cru em vez de retornar `navegador_perdido` pra interface. **Corrigido:** `except Exception` genérico ao redor de `abrir_sessao_siafe`, tratando qualquer erro na abertura de sessão do grupo como `navegador_perdido`.

### 7. `fechar_sessao_siafe`/aba nova — causa raiz do bug 6, eliminada pela raiz
Investigando o bug 6 mais a fundo: `fechar_sessao_siafe` (que fechava a aba ao fim de cada grupo, pensando em manter múltiplas sessões) fechava a **única** aba aberta do driver (nunca há mais de uma sessão ativa por vez neste fluxo) — deixando o driver sem nenhuma janela ativa. A tentativa seguinte de abrir aba nova pro próximo grupo falhava com `"target window already closed"`. **Corrigido pela raiz (a pedido do usuário):** removido o conceito de aba nova/fechar aba por completo. `abrir_sessao_siafe` não recebe mais `nova_aba` — cada grupo simplesmente navega (`abrir_url`) na mesma aba/driver e loga de novo. `fechar_sessao_siafe` foi removida de `services.py`.

### Estado no fim da sessão 3
Teste ao vivo com 11 GRs pendentes em 2 grupos (`versao=1,ano=2026` com 10 e `versao=4,ano=2023` com 1) confirmou: todos os 10 do grupo 1 baixaram sem erro de filtro/timeout (incluindo os 3 que antes travavam na troca de modo). O crash na transição pro grupo 2 (`target window already closed`) foi o motivo do fix #7, ainda não re-testado ao vivo depois dessa correção — próximo teste deve confirmar que múltiplos grupos processam em sequência sem reiniciar o driver e sem crash na troca.
