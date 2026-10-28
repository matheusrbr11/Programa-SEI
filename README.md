# 📑 Programa SEI

> Automação da instrução de processos de Crédito em Conta e Depósito Judicial no SEI, desenvolvida pela **Equipe de Otimização Processual (EOP/SUPCONFI)** do **Tesouro do Estado do Rio de Janeiro**.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://www.python.org/)
[![Selenium](https://img.shields.io/badge/Selenium-WebDriver-green?logo=selenium)](https://www.selenium.dev/)
[![CustomTkinter](https://img.shields.io/badge/UI-CustomTkinter-informational)](https://github.com/TomSchimansky/CustomTkinter)
[![SQLite](https://img.shields.io/badge/Database-SQLite-lightgrey?logo=sqlite)](https://www.sqlite.org/)
[![Versão](https://img.shields.io/badge/Versão-1.5.2-orange)](.)

---

## 📋 Sumário

- [Sobre o Projeto](#-sobre-o-projeto)
- [Funcionalidades](#-funcionalidades)
- [Arquitetura](#️-arquitetura)
- [Pré-requisitos](#️-pré-requisitos)
- [Instalação](#-instalação)
- [Estrutura de Arquivos](#-estrutura-de-arquivos)
- [Como Usar](#️-como-usar)
- [Banco de Dados](#️-banco-de-dados)
- [Códigos de Saída](#-códigos-de-saída)
- [Contribuição](#-contribuição)

---

## 📌 Sobre o Projeto

O **Programa SEI** é uma ferramenta de automação desenvolvida para a **Superintendência de Controles Financeiros (SUPCONFI)** do Tesouro do Estado do Rio de Janeiro, com o objetivo de automatizar a instrução de processos de **Crédito em Conta** e **Depósito Judicial** no sistema **SEI**.

O sistema elimina a necessidade de localizar manualmente comprovantes de resgate, guias de recolhimento e dados de conta judicial, reduzindo erros operacionais e o tempo gasto na instrução processual, processando automaticamente:

- Localização e leitura dos anexos do processo (Comprovante de Resgate, Agendamento, Ofício, Alvará Eletrônico, Alvará de Levantamento, Mandado de Pagamento)
- Extração dos dados financeiros
- Consulta ao Banco do Brasil quando a conta judicial não está explícita no anexo
- Localização ou download da Guia de Recolhimento (GR) correspondente no SIAFE-Rio
- No módulo de Depósito Judicial, geração do Comprovante DJO (planilha de Resgate convertida em PDF) a partir do movimento diário do Banco do Brasil
- Inclusão do despacho padrão e conclusão do processo no SEI

Os dois módulos — **Crédito em Conta** e **Depósito Judicial** — seguem o mesmo fluxo de duas etapas (coleta e finalização), com marcadores e regras de negócio próprias, mas compartilham o mesmo código de fluxo, a mesma tabela de checkpoint, a mesma interface e a mesma biblioteca de automação SEI/SIAFE.

---

## ✨ Funcionalidades

### 1. Interface Gráfica (GUI)
- Tela de login com autenticação via usuário e senha do SEI
- Menu principal com acesso aos módulos **Crédito em Conta** e **Depósito Judicial**
- Tela de execução com log em tempo real, barra de progresso e botão de cancelamento
- Notificação automática de erros críticos por e-mail e/ou Microsoft Teams ao encerrar o programa

### 2. Etapa 1 — Processar Processos (Coleta)
- Autenticação no **SIAFE-Rio**
- Varredura dos processos marcados no SEI com o marcador `PGE - Credito em Conta - Processar` (Crédito em Conta) ou `PGE - Deposito Judicial - Processar` (Depósito Judicial)
- Extração dos dados do anexo da PGE via **pdfplumber**, com estratégias dedicadas por tipo de documento
- Consulta ao site de resgate do Banco do Brasil via **Selenium** quando necessário
- No Depósito Judicial, geração da planilha de Resgate a partir do movimento diário do BB e conversão para o Comprovante DJO em PDF
- Busca e download da Guia de Recolhimento no SIAFE-Rio, agrupando processos por versão/ano para reaproveitar a mesma sessão logada

### 3. Etapa 2 — Responder Processos (Finalização)
- Reaproveita o login do SEI já realizado na abertura do programa
- Anexa o comprovante (e, no Depósito Judicial, também o Comprovante DJO) e a Guia de Recolhimento ao processo
- Inclui o despacho padrão
- Inclui o processo no bloco de assinatura da COOCB e troca o marcador para `PGE - Credito em Conta - Concluido` ou `PGE - Deposito Judicial - Concluido`, conforme o módulo

---

## 🏗️ Arquitetura

```
┌───────────────────────────────────────────────────────────┐
│                        exe.py                             │
│               (ponto de entrada / atalho)                 │
└───────────────────┬───────────────────────────────────────┘
                    │ subprocess
                    ▼
┌───────────────────────────────────────────────────────────┐
│                    main.py                                │
│              SeiApp (CustomTkinter + eop_ui)              │
│  ┌────────────────┐        ┌───────────────────────────┐  │
│  │  GUI / Login   │        │   Execução / Progresso    │  │
│  └────────┬───────┘        └────────────┬──────────────┘  │
│           │                              │                │
│           ▼                              ▼                │
│  ┌────────────────┐        ┌───────────────────────────┐  │
│  │ run_processar_ │        │      jupiter / eop_ui     │  │
│  │ cc.py / dj.py  │        │   Automação SEI/SIAFE     │  │
│  └────────┬───────┘        └───────────────────────────┘  │
│           │                                               │
│           ▼                                               │
│  ┌─────────────────────────────────────────────────────┐  │
│  │   analise_comum/orchestrator.py (fluxo único)       │  │
│  │   + modalidades/cc.py · modalidades/dj.py           │  │
│  └────────┬────────────────────────────────────────────┘  │
│           │                                               │
│           ▼                                               │
│  ┌────────────────┐                                       │
│  │  SQLite (.db)  │                                       │
│  └────────────────┘                                       │
└───────────────────────────────────────────────────────────┘
```

Crédito em Conta e Depósito Judicial são duas **modalidades** do mesmo fluxo. O fluxo (`analise_comum/orchestrator.py`) é um só; cada modalidade (`analise_comum/modalidades/cc.py` e `dj.py`) define seus marcadores, contas, texto padrão do despacho, ordem dos extratores e os pontos em que difere: de onde vem o valor da GR, os documentos que gera (no DJ, o Comprovante DJO) e como monta o despacho. A interface (`main.py`) aciona cada modalidade no seu próprio subprocesso (`run_processar_cc.py` / `run_processar_dj.py`).

Para incluir uma modalidade nova: crie `analise_comum/modalidades/<codigo>.py` com uma subclasse de `Modalidade`, registre-a em `MODALIDADES` (`modalidades/__init__.py`), inclua as colunas próprias em `COLUNAS_POR_MODALIDADE` (`repositorio.py`) e um `run_processar_<codigo>.py`.

**Fluxo de dados:**

```
Processos marcados no SEI → [Etapa 1] → extrai dados + (DJ: gera Comprovante DJO) + baixa GR
                                              │
                        tabela processos (modalidade 'cc' / 'dj')
                                              │
                                    [Etapa 2] → anexa, despacha, conclui
                                              │
                                    Processo instruído no SEI
```

---

## ⚙️ Pré-requisitos

- **Python** 3.10 ou superior
- **Microsoft Edge** instalado (WebDriver compatível com a versão do navegador)
- **Microsoft Edge WebDriver** no PATH do sistema
- Acesso à rede corporativa do Tesouro (banco de dados, logs e pasta de GRs)
- Credenciais válidas no **SEI** e no **SIAFE-Rio**

---

## 🚀 Instalação

### 1. Clone o repositório

```bash
git clone <https://github.com/matheusrbr11/Programa-SEI>
cd "Programa SEI"
```

### 2. Crie e ative um ambiente virtual

```bash
python -m venv env
# Windows
env\Scripts\activate
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
```

> **Dependências principais:**

| Pacote | Uso |
|---|---|
| `customtkinter` | Interface gráfica |
| `selenium` / `undetected-chromedriver` | Automação do navegador |
| `pdfplumber` | Extração de texto de PDFs |
| `num2words` | Conversão de valores por extenso |
| `pandas` | Formatação de timestamps no resumo de erros |
| `TesouroEOP-UI` | Biblioteca da EOP/SUPCONFI para geração da Interface Gráfica |
| `jupiter-subtes` | Biblioteca da EOP/SUPCONFI para automação do SIAFE-Rio2 |

### 4. Execute o programa

```bash
python main.py
```

---

## 📁 Estrutura de Arquivos

```
Programa SEI/
│
├── exe.py                   # Ponto de entrada (usado pelo atalho .exe)
├── main.py                  # Aplicação principal + GUI (SeiApp)
├── run_processar_cc.py      # Subprocesso da modalidade Crédito em Conta
├── run_processar_dj.py      # Subprocesso da modalidade Depósito Judicial
├── config.example.json      # Modelo de configuração
│
├── ambiente.py              # Produção/Homologação e caminhos que dependem dele
├── migrar_tabela_processos.py  # Migração única das tabelas antigas para a tabela processos
│
├── analise_comum/           # Todo o código dos fluxos
│   ├── main.py               #   Entry-point: lê credenciais e executa a etapa da modalidade
│   ├── orchestrator.py       #   Fluxo único: coleta (Etapa 1), GR em lote no SIAFE e finalização (Etapa 2)
│   ├── services.py            #   Integrações: BB, contabilizações, SIAFE e helpers do SEI
│   ├── repositorio.py         #   Persistência: tabela processos (todas as modalidades) no hermes.db
│   ├── extractors.py         #   Extração de dados dos PDFs (comprovante, ofício, alvará, mandado)
│   ├── core.py                #   Exceções e setup de log do subprocesso
│   ├── log.py                 #   Logger do fluxo em execução
│   ├── utils.py               #   Funções utilitárias
│   ├── config.py              #   Constantes comuns, URLs, caminhos
│   └── modalidades/          #   O que muda entre as modalidades
│       ├── __init__.py        #     Classe base Modalidade e registro das modalidades
│       ├── cc.py              #     Crédito em Conta: configuração, extratores e despacho
│       ├── dj.py              #     Depósito Judicial: configuração, extratores, despacho, Comprovante DJO e SharePoint
│       ├── planilha.py        #     DJ: geração da planilha de Resgate
│       └── pdf.py             #     DJ: conversão da planilha de Resgate para PDF
│
├── planilhas/
│   ├── Resgate Modelo.xlsx                   # Template usado na geração da planilha de Resgate (Comprovante DJO)
│   └── RESGATES A FAVOR DO GOVER *.xlsx       # Exemplo de planilha diária de resgates do Banco do Brasil
│
├── driver/
│   └── msedgedriver.exe      # WebDriver do Edge
│
├── dist/
│   └── exe.exe                # Executável .exe
│
├── img/
│   ├── icon.ico            # Ícone do programa
|   ├── icon2.png           # Ícone do programa em PNG
|   ├── tesouro.png         # Logo do Tesouro RJ
│   └── voltar.png          # Ícone de voltar
│
├── Manual de Uso.pdf       # Manual do usuário
├── requirements.txt        # Dependências do projeto
├── .gitignore
└── README.md
```

---

## 🖥️ Como Usar

### Passo 1 — Login
Abra o programa pelo atalho ou via `exe.py`. Na tela de login, insira seu **usuário** e **senha** do SEI e clique em **LOGIN**.

### Passo 2 — Menu Principal
No menu principal, escolha o módulo desejado: **Crédito em Conta** ou **Depósito Judicial**. Os dois seguem exatamente o mesmo fluxo de duas etapas, cada um filtrando pelo seu próprio marcador no SEI.

### Passo 3 — Processar Processos (Etapa 1)
Clique em **PROCESSAR PROCESSOS**. Na primeira vez da sessão, o programa pede o login do **SIAFE-Rio** (CPF e senha); nas próximas, reaproveita a credencial já validada. Em seguida, o programa irá:
1. Percorrer os processos marcados no SEI com o marcador do módulo escolhido
2. Extrair os dados dos anexos e, se necessário, consultar o Banco do Brasil
3. No Depósito Judicial, gerar a planilha de Resgate a partir do movimento diário do BB e convertê-la no Comprovante DJO em PDF
4. Localizar ou baixar a Guia de Recolhimento no SIAFE-Rio
5. Registrar o andamento na tabela de controle do banco de dados

> ⚠️ Esta etapa **não conclui** os processos no SEI. Ela apenas coleta os dados necessários.

### Passo 4 — Responder Processos (Etapa 2)
Clique em **RESPONDER PROCESSOS**. O programa reaproveita o login do SEI já feito e, para cada processo com dados coletados, anexa os documentos (comprovante de resgate, Comprovante DJO quando aplicável, e Guia de Recolhimento), inclui o despacho padrão e troca o marcador para concluído.

---

## 🗄️ Banco de Dados

O programa mantém uma tabela de checkpoint, `processos`, criada dinamicamente na primeira execução. Cada processo tem uma única modalidade e, portanto, uma única linha:

| Coluna | Tipo | Descrição |
|---|---|---|
| `processo` | TEXT | Número do processo no SEI (único na tabela) |
| `modalidade` | TEXT | `cc` (Crédito em Conta) ou `dj` (Depósito Judicial) |
| `status` | TEXT | Situação atual (`pendente`, `dados_coletados`, `aguardando_gr`, `concluido`, `erro_coleta`, `ignorado`, `revisar_despacho`, `debug`) |
| `conta` | TEXT | Conta bancária identificada |
| `conta_judicial` | TEXT | Conta judicial identificada |
| `processo_judicial` | TEXT | Número do processo judicial de origem |
| `data_pagamento` / `data_alvara` | TEXT | Datas extraídas dos documentos |
| `ano` | INTEGER | Ano do documento/exercício |
| `valor_pesquisa` | REAL | Valor usado na busca da GR |
| `caminho_comprovante` / `caminho_gr` | TEXT | Caminhos locais do Comprovante de Resgate e da Guia de Recolhimento baixados |
| `num_doc` | TEXT | Número do documento no SIAFE |
| `cnpj` | TEXT | CNPJ do beneficiário |
| `tem_gr` / `tem_comprovante` / `tem_despacho_apos_gr` | INTEGER | Flags de controle da instrução |
| `usuario_resposta` | TEXT | Login do usuário que finalizou o processo |
| `data_hora_resposta` | TEXT | Horário da finalização |
| `tempo_resposta` | REAL | Tempo de execução da Etapa 2 |

Colunas preenchidas só na modalidade `dj` (nulas, ou 0 nas flags, em `cc`):

| Coluna | Tipo | Descrição |
|---|---|---|
| `valor_resgate` / `valor_30` | REAL | Valores extraídos dos documentos |
| `caminho_comprovante_djo` | TEXT | Caminho local do Comprovante DJO (PDF gerado a partir da planilha de Resgate) |
| `reu` | TEXT | Réu identificado no processo judicial |
| `titulo_documento` / `numero_documento` | TEXT | Identificação do documento de origem (ofício, alvará, mandado) |
| `tem_comprovante_djo` | INTEGER | Flag de controle da instrução |

A tabela vive no `hermes.db`, definido em `CAMINHO_HERMES`. Cada fluxo lê e grava só os registros da sua modalidade. Um processo só aparece nos dois fluxos se foi colocado no marcador errado:

- registrado na outra modalidade como `ignorado`: o fluxo atual assume o processo (o marcador foi corrigido);
- registrado na outra modalidade com outro status: o fluxo pula o processo e avisa no log para conferir o marcador.

No modo DEBUG, marque `status = 'debug'` no registro com a modalidade certa.

As tabelas antigas (`processos_credito_conta` / `processos_deposito_judicial`) não são mais usadas. Para trazer os registros delas, rode `migrar_tabela_processos.py` com o Hermes e o Programa-SEI fechados e a tabela `processos` ainda vazia: sem `--aplicar` ele só mostra o que faria; com `--aplicar` faz backup do banco, migra e deixa as antigas como `*_old`, com views só de leitura no nome antigo.

O checkpoint permite retomar de onde parou caso o programa seja interrompido entre as etapas — inclusive entre as sub-fases da Etapa 1.

---

## 🔢 Códigos de Saída

O subprocesso de automação devolve um código de saída ao final de cada etapa, interpretado pela interface:

| Código | Significado |
|---|---|
| `0` | Sucesso total |
| `1` | Sucesso parcial (alguns processos pulados) |
| `2` | Falha de login no SEI |
| `3` | Falha de login no SIAFE |
| `4` | Navegador ou sessão perdida |
| `5` | Erro crítico durante a automação |

---

## 🤝 Contribuição

Este projeto é desenvolvido e mantido pela **Equipe de Otimização Processual (EOP)** da **SUPCONFI — Tesouro do Estado do Rio de Janeiro**.

Dúvidas, sugestões e reportes de inconsistências operacionais devem ser encaminhados diretamente à equipe. Em caso de mudanças nas premissas operacionais (marcadores do SEI, roteiros de despacho, estrutura dos documentos do BB, etc.), a equipe deve ser notificada para atualização do sistema e da documentação.

---

<div align="center">
  <sub>EOP / SUPCONFI — Tesouro do Estado do Rio de Janeiro &nbsp;|&nbsp; Versão 1.5.2</sub>
</div>
