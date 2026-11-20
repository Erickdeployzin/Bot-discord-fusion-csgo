# FUSION BOT — Sistema Lite de Gerenciamento Discord & CS:GO Legacy

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x%20Async-D71F00?style=for-the-badge)
![MySQL](https://img.shields.io/badge/MySQL-8.0%2B%20%7C%20MariaDB-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![Mercado Pago](https://img.shields.io/badge/Mercado%20Pago-PIX%20Oficial-00B1EA?style=for-the-badge)

Sistema completo de gerenciamento de comunidades Discord desenvolvido **100% em Python 3.12+**, integrando **discord.py 2.x**, **FastAPI**, **SQLAlchemy 2.x Assíncrono (MySQL/PostgreSQL)**, **Alembic**, **Mercado Pago PIX Oficial**, **APScheduler**, **Criptografia Fernet** e **Protocolo Valve A2S + RCON + SourceMod REST** para os servidores CS:GO Legacy e portal web da **FUSION**.

---

## Sumário

1. [Arquitetura e Módulos do Sistema](#1-arquitetura-e-módulos-do-sistema)
2. [Estrutura de Diretórios](#2-estrutura-de-diretórios)
3. [Instalação Local e Preparação do Banco de Dados](#3-instalação-local-e-preparação-do-banco-de-dados)
4. [Configuração das Variáveis de Ambiente (.env) e .gitignore](#4-configuração-das-variáveis-de-ambiente-env-e-gitignore)
5. [Configuração no Discord Developer Portal](#5-configuração-no-discord-developer-portal)
6. [Integração CS:GO Legacy (A2S, RCON e SourceMod)](#6-integração-csgo-legacy-a2s-rcon-e-sourcemod)
7. [Integração Mercado Pago (PIX Automático e Webhooks)](#7-integração-mercado-pago-pix-automático-e-webhooks)
8. [Guia Completo de Erros Conhecidos, Possíveis Falhas e Soluções](#8-guia-completo-de-erros-conhecidos-possíveis-falhas-e-soluções)
9. [Roadmap de Melhorias Técnicas (Código, Segurança e Performance)](#9-roadmap-de-melhorias-técnicas-código-segurança-e-performance)
10. [Execução 24/7 em Produção (Systemd + Nginx SSL)](#10-execução-247-em-produção-systemd--nginx-ssl)

---

## 1. Arquitetura e Módulos do Sistema

O **FUSION BOT** executa três serviços assíncronos de forma concorrente dentro do mesmo *event loop* (`asyncio.gather` em `main.py`):

* **Bot Discord (`discord.py 2.x`)**: Gerencia Slash Commands (`/painel` e comandos públicos), Views persistentes, seletores nativos (`UserSelect`, `RoleSelect`, `ChannelSelect`) e Modais interativos.
* **API & Webhooks (`FastAPI + Uvicorn`)**: Recebe notificações instantâneas de pagamento PIX do Mercado Pago, expõe endpoints REST para plugins SourceMod nos servidores CS:GO e fornece monitoramento de integridade.
* **Agendador Background (`APScheduler` — `FusionScheduler`)**: Executa rotinas periódicas a cada 1 minuto para cancelar pedidos PIX expirados, devolver itens reservados ao estoque, expirar VIPs vencidos e remover cargos/benefícios expirados.
* **Persistência Assíncrona (`SQLAlchemy 2.x + aiomysql`)**: Comunicação não-bloqueante com MySQL/MariaDB, utilizando criptografia simétrica **Fernet (AES-128-CBC + HMAC-SHA256)** para proteger senhas RCON e tokens de pagamento armazenados no banco.

### Tabela de Módulos

| Módulo | Diretório / Arquivos | Funcionalidades Principais |
| :--- | :--- | :--- |
| **Painel Administrativo** | `bot/cogs/admin_cog.py`<br>`bot/views/panel_views.py`<br>`bot/modals/panel_modals.py` | Central administrativa unificada via `/painel` para gerenciar canais de log, permissões de módulos, credenciais Mercado Pago, catálogo de produtos, estoque digital, planos VIP, tickets e servidores CS:GO. |
| **Loja & Pagamentos PIX** | `modules/sales/` | Carrinho interativo, aplicação de cupons de desconto, geração automática de QR Code e código Copia e Cola PIX via Mercado Pago, controle de reserva temporária e entrega automática. |
| **Gestão de VIPs** | `modules/vip/` | Planos VIP configuráveis por duração, atribuição e remoção automática de cargos no Discord e sincronização de privilégios nos servidores CS:GO via SteamID. |
| **CS:GO Legacy & RCON** | `modules/csgo/` | Consulta em tempo real via Valve A2S (mapa atual, jogadores conectados, ping), envio remoto de comandos RCON com senha criptografada, vinculação Discord ↔ SteamID (`STEAM_0:...` / `SteamID64`) e estatísticas/histórico de partidas. |
| **Tickets & Suporte** | `modules/tickets/` | Painéis personalizáveis por departamento, reivindicação (*claim*) por equipe staff, salvamento completo do histórico de mensagens e avaliação de atendimento por estrelas. |
| **Logs & Auditoria** | `modules/logs/`<br>`bot/events/guild_events.py` | Captura global de exceções em comandos e registro estruturado de compras, ações administrativas, comandos RCON e fechamento de tickets em canais dedicados. |

---

## 2. Estrutura de Diretórios

```text
fusion_bot/
├── main.py                      # Ponto de entrada: inicializa Discord Bot, FastAPI e Scheduler
├── requirements.txt             # Dependências Python do projeto
├── .env.example                 # Modelo de variáveis de ambiente
├── .gitignore                   # Regras de arquivos ignorados pelo Git
├── alembic.ini                  # Configuração do Alembic para migrations
│
├── api/                         # Rotas FastAPI (Webhooks Mercado Pago, SourceMod REST, Health)
├── bot/
│   ├── cogs/
│   │   └── admin_cog.py         # Slash command /painel e comandos de administração
│   ├── events/
│   │   └── guild_events.py      # Listeners de eventos e handler global de erros
│   ├── modals/
│   │   └── panel_modals.py      # Modais de cadastro (Servidor CS:GO, VIP, Produtos, MP)
│   └── views/
│       └── panel_views.py       # Views interativas, botões e seletores do painel
│
├── core/
│   ├── config.py                # Leitura e validação das configurações do .env
│   ├── database.py              # Engine assíncrona SQLAlchemy e gerenciador de sessões
│   └── security.py              # Criptografia Fernet (senhas RCON e credenciais sensíveis)
│
├── database/
│   ├── models/                  # Modelos ORM das 22 tabelas do sistema
│   └── migrations/              # Ambiente e histórico de migrações Alembic
│       ├── env.py
│       └── versions/
│
├── modules/
│   ├── csgo/                    # Integração A2S, RCON, Steam Link, stats e partidas
│   ├── logs/                    # Serviço de despacho de logs para canais do Discord
│   ├── sales/                   # Loja, carrinho, pedidos, estoque digital, cupons e MP
│   ├── tickets/                 # Tickets multidepartamento, mensagens e avaliações
│   └── vip/                     # Assinaturas VIP e controle de expiração
│
└── tasks/
    └── scheduler.py             # Tarefas agendadas (expiração de pedidos e VIPs)
```

---

## 3. Instalação Local e Preparação do Banco de Dados

### Pré-requisitos
* **Python 3.12** ou superior (testado em Python 3.12, 3.13 e 3.14)
* **MySQL 8.0+** ou **MariaDB 10.6+** (ou **PostgreSQL 15+**)
* Acesso SSH em VPS Linux (Ubuntu 22.04 / 24.04) para hospedagem 24/7 ou Windows 10/11 para desenvolvimento local

---

### Passo a Passo — VPS Linux (Ubuntu 22.04 / 24.04)

```bash
# 1. Acesse o diretório do projeto e crie o ambiente virtual
cd /opt/fusion_bot
python3.12 -m venv venv
source venv/bin/activate

# 2. Atualize o pip e instale todas as dependências
pip install --upgrade pip
pip install -r requirements.txt

# 3. Copie o arquivo de ambiente e gere a chave de criptografia Fernet
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copie a chave Fernet exibida no terminal (44 caracteres terminando em `=`) e cole em `ENCRYPTION_KEY=` no seu `.env`.

---

### Passo a Passo — Windows (PowerShell)

> **Importante:** Evite hospedar o projeto dentro de `C:\Users\...\Documents` se o **Acesso a Pastas Controladas** do Windows Defender estiver ativo, pois ele bloqueia o `python.exe` de criar arquivos de migration e logs. Utilize uma pasta dedicada como `C:\bots\fusion_bot`.

```powershell
# 1. Acesse o diretório do projeto e crie o ambiente virtual
cd C:\bots\fusion_bot
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Atualize o pip e instale todas as dependências
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Copie o arquivo de ambiente e salve automaticamente uma chave Fernet válida no .env
Copy-Item .env.example .env
$k = .\venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
$envText = Get-Content .env -Raw
if ($envText -match '(?m)^ENCRYPTION_KEY=.*') {
    $envText = $envText -replace '(?m)^ENCRYPTION_KEY=.*', "ENCRYPTION_KEY=$k"
} else {
    $envText += "`r`nENCRYPTION_KEY=$k`r`n"
}
Set-Content .env -Value $envText -NoNewline
Write-Host "Chave Fernet configurada no .env: $k"
```

---

### Criação do Banco de Dados e das Tabelas

#### 1. Crie o banco de dados e o usuário no MySQL

Conecte-se ao seu servidor MySQL (`sudo mysql` no Linux, ou HeidiSQL / phpMyAdmin no Windows) e execute:

```sql
CREATE DATABASE IF NOT EXISTS fusion_bot
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'fusion_user'@'localhost' IDENTIFIED BY 'SUA_SENHA_FORTE';
CREATE USER IF NOT EXISTS 'fusion_user'@'127.0.0.1' IDENTIFIED BY 'SUA_SENHA_FORTE';

GRANT ALL PRIVILEGES ON fusion_bot.* TO 'fusion_user'@'localhost';
GRANT ALL PRIVILEGES ON fusion_bot.* TO 'fusion_user'@'127.0.0.1';
FLUSH PRIVILEGES;
```

#### 2. Gere e aplique a estrutura das 22 tabelas

**Opção A — Via Alembic (quando a pasta `database/migrations/versions` possui permissão de escrita):**
```bash
mkdir -p database/migrations/versions
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head
```

**Opção B — Via SQLAlchemy Direto (recomendado se o Alembic não tiver permissão para criar arquivos na pasta):**
```bash
python -c "import sys, asyncio, pkgutil, importlib, database.models as m; from core.database import engine; [importlib.import_module(f'{m.__name__}.{i.name}') for i in pkgutil.iter_modules(getattr(m, '__path__', []))]; meta = next(v.metadata for mod in list(sys.modules.values()) if getattr(mod, '__name__', '').startswith('database') for v in vars(mod).values() if hasattr(v, 'metadata') and hasattr(v, '__tablename__')); ns = {'engine': engine, 'meta': meta}; exec('async def run():\n async with engine.begin() as c: await c.run_sync(meta.create_all)\n await engine.dispose()', ns); asyncio.run(ns['run']()); print('Tabelas criadas com sucesso!')"
```

**Tabelas criadas no banco:**
* **Administração & Logs**: `guild_configs`, `guild_log_channels`, `guild_mercadopago_configs`, `guild_module_permissions`, `automated_messages`
* **Loja & Checkout PIX**: `products`, `digital_stock_items`, `coupons`, `carts`, `cart_items`, `orders`, `order_items`, `payment_transactions`
* **Sistema VIP**: `vip_plans`, `user_vips`
* **CS:GO Legacy**: `csgo_servers`, `csgo_player_stats`, `csgo_match_history`, `user_steam_links`
* **Suporte & Tickets**: `ticket_panels`, `ticket_departments`, `tickets`, `ticket_messages`, `ticket_ratings`

#### 3. Inicie o bot

```bash
python main.py
```

Saída esperada quando tudo está configurado corretamente:
```text
INFO | fusion | Extensão carregada: bot.cogs.admin_cog
INFO | fusion | Extensão carregada: bot.events.guild_events
INFO | fusion | Extensão carregada: modules.tickets.cog
INFO | fusion | Extensão carregada: modules.sales.cog
INFO | fusion | Extensão carregada: modules.vip.cog
INFO | fusion | Extensão carregada: modules.csgo.cog
INFO | fusion | Slash commands sincronizados globalmente: 15
INFO | fusion | FusionScheduler iniciado com sucesso.
INFO | fusion | FUSION BOT conectado como SeuBot#0000 (ID: 000000000000000000) | Servidores: 1
```

---

## 4. Configuração das Variáveis de Ambiente (.env)

### Modelo `.env`

```env
# ==========================================
# AMBIENTE DA APLICAÇÃO
# ==========================================
APP_ENV=production
LOG_LEVEL=INFO

# ==========================================
# DISCORD BOT
# ==========================================
DISCORD_TOKEN=SEU_TOKEN_DO_BOT_DISCORD
DISCORD_CLIENT_ID=ID_DA_APLICACAO_DISCORD

# ==========================================
# BANCO DE DADOS ASSÍNCRONO
# Formato: mysql+aiomysql://USUARIO:SENHA@HOST:PORTA/BANCO?charset=utf8mb4
# ATENÇÃO: Não coloque ":" extra após a senha antes do "@"
# ==========================================
DATABASE_URL=mysql+aiomysql://fusion_user:SUA_SENHA_FORTE@127.0.0.1:3306/fusion_bot?charset=utf8mb4
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# ==========================================
# CHAVE DE CRIPTOGRAFIA FERNET (OBRIGATÓRIO)
# Deve conter exatamente 32 bytes codificados em base64 url-safe (44 chars)
# ==========================================
ENCRYPTION_KEY=COLE_AQUI_A_CHAVE_GERADA_PELO_FERNET

# ==========================================
# API WEB & WEBHOOKS (FASTAPI + UVICORN)
# ==========================================
API_HOST=0.0.0.0
API_PORT=8080
PUBLIC_BASE_URL=[https://api.seudominio.com.br](https://api.seudominio.com.br)

# ==========================================
# INTEGRAÇÃO SOURCEMOD / PORTAL WEB
# ==========================================
SOURCEMOD_API_SECRET=CHAVE_SECRETA_COMPARTILHADA_COM_O_SERVIDOR_CSGO
```
---

## 5. Configuração no Discord Developer Portal

1. Acesse o [Discord Developer Portal](https://discord.com/developers/applications) e abra sua aplicação.
2. No menu lateral **Bot**, habilite as **Privileged Gateway Intents**:
   * **Presence Intent** *(recomendado)*
   * **Server Members Intent** *(obrigatório para detectar entrada/saída de membros e gerenciar cargos VIP)*
   * **Message Content Intent** *(obrigatório para registrar transcrições de tickets e comandos)*
3. No menu **OAuth2 → URL Generator**:
   * Selecione os escopos: `bot` e `applications.commands`.
   * Em **Bot Permissions**, selecione `Administrator` (ou conceda individualmente: *Gerenciar Cargos*, *Gerenciar Canais*, *Enviar Mensagens*, *Gerenciar Mensagens*, *Incorporar Links*, *Anexar Arquivos* e *Ler Histórico de Mensagens*).
4. Adicione o bot ao seu servidor usando o link gerado e execute `/painel` em um canal restrito à administração.

---

## 6. Integração CS:GO Legacy (A2S, RCON e SourceMod)

1. **Configuração no `server.cfg` do servidor CS:GO**:
   ```cfg
   rcon_password "SUA_SENHA_RCON_FORTE"
   sv_rcon_banpenalty 0
   sv_rcon_maxfailures 10
   host_name_store 1
   host_info_show 2
   host_players_show 2
   ```
2. **Parâmetro de inicialização do SRCDS**:
   * Certifique-se de iniciar o servidor com `-usercon` na linha de comando (`srcds_run` / `srcds.exe`) para habilitar conexões TCP RCON.
3. **Regras de Firewall e DNS**:
   * Libere a porta do servidor (ex.: `27015` ou `30060`) nos protocolos **TCP** (RCON) e **UDP** (Valve A2S Query) para o IP da máquina onde o bot está hospedado.
   * **Não utilize proxy laranja da Cloudflare** no domínio cadastrado como endereço do servidor CS:GO, pois o proxy HTTP da Cloudflare bloqueia pacotes UDP/A2S e TCP RCON. Utilize o **IP numérico direto** ou um subdomínio com nuvem cinza (*DNS Only*).
4. **Cadastro via `/painel`**:
   * Execute `/painel` → **Servidores CS:GO** → **Adicionar Servidor** e preencha Nome, Tipo (`MIX`, `RETAKE`, `DM`, `4FUN`), Endereço IP, Porta e Senha RCON. A senha será criptografada automaticamente antes de ser salva no MySQL.

---

## 7. Integração Mercado Pago (PIX Automático e Webhooks)

1. Acesse o [Painel de Desenvolvedores do Mercado Pago](https://www.mercadopago.com.br/developers/panel/app) e crie uma aplicação de **Pagamentos Online**.
2. Copie o **Access Token de Produção** (`APP_USR-...`).
3. No Discord, abra `/painel` → **Configurar Mercado Pago** e cadastre o token (armazenado criptografado no banco por servidor).
4. **Configuração de Webhook**:
   * O FastAPI recebe notificações na porta definida em `API_PORT` (`8080`).
   * Em produção, utilize Nginx como proxy reverso com certificado SSL (`https://`) e informe essa URL pública em `PUBLIC_BASE_URL`, pois o Mercado Pago exige HTTPS válido para disparar notificações de pagamento aprovado.

---

## 8. Guia Completo de Erros Conhecidos, Possíveis Falhas e Soluções

Esta seção documenta todos os erros reais encontrados durante a configuração e execução do sistema, suas causas técnicas e os comandos exatos para correção.

---

### 1. `AttributeError: module 'discord.ui' has no attribute 'user_select'`

* **Erro exibido:**
  ```text
  AttributeError: module 'discord.ui' has no attribute 'user_select'
  ```
* **Causa:** No `discord.py 2.x`, seletores nativos (usuários, cargos, canais e mencionáveis) não possuem decoradores próprios como `@discord.ui.user_select()`, devendo utilizar `@discord.ui.select(cls=discord.ui.UserSelect, ...)`.
* **Solução:** Substitua nos arquivos `.py` das Views:
  * `@discord.ui.user_select(` → `@discord.ui.select(cls=discord.ui.UserSelect, `
  * `@discord.ui.role_select(` → `@discord.ui.select(cls=discord.ui.RoleSelect, `
  * `@discord.ui.channel_select(` → `@discord.ui.select(cls=discord.ui.ChannelSelect, `
  * `@discord.ui.mentionable_select(` → `@discord.ui.select(cls=discord.ui.MentionableSelect, `

  **Comando automático (PowerShell):**
  ```powershell
  Get-ChildItem -Recurse -Filter *.py | Where-Object { $_.FullName -notmatch '\\venv\\' } | ForEach-Object { $c = Get-Content $_.FullName -Raw -Encoding UTF8; $u = $c -replace '@discord\.ui\.user_select\(', '@discord.ui.select(cls=discord.ui.UserSelect, ' -replace '@discord\.ui\.role_select\(', '@discord.ui.select(cls=discord.ui.RoleSelect, ' -replace '@discord\.ui\.channel_select\(', '@discord.ui.select(cls=discord.ui.ChannelSelect, ' -replace '@discord\.ui\.mentionable_select\(', '@discord.ui.select(cls=discord.ui.MentionableSelect, '; if ($c -ne $u) { Set-Content $_.FullName -Value $u -Encoding UTF8 -NoNewline; Write-Host "Corrigido: $($_.Name)" } }
  ```

---

### 2. `pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1'")` / `[WinError 1225]`

* **Erro exibido:**
  ```text
  pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1'")
  ConnectionRefusedError: [WinError 1225] O computador remoto recusou a conexão de rede
  ```
* **Causa:** O servidor MySQL/MariaDB está desligado no host configurado em `DATABASE_URL` ou a porta `3306` não está aceitando conexões.
* **Solução:**
  * **Linux:**
```bash
sudo systemctl start mysql
sudo systemctl enable mysql
```
  * **Windows:** Inicie o MySQL pelo painel do **Laragon** / **XAMPP** ou abra `services.msc` e inicie o serviço `MySQL80`.
  * **Alternativa para testes locais sem MySQL (SQLite):** Instale `aiosqlite` (`pip install aiosqlite`), defina `DATABASE_URL=sqlite+aiosqlite:///./fusion_local.db` no `.env` e ajuste `core/database.py` para não passar `pool_size`/`max_overflow` quando a URL começar com `sqlite`.

---

### 3. `pymysql.err.OperationalError: (1045, "Access denied for user '...'@'...' (using password: YES)")`

* **Erro exibido:**
  ```text
  pymysql.err.OperationalError: (1045, "Access denied for user 'usuario'@'ip_origem' (using password: YES)")
  ```
* **Causa:**
  1. Caractere `:` sobrando após a senha na `DATABASE_URL` (ex.: `mysql+aiomysql://user:senha:@host:3306/db` faz o driver enviar `senha:` como senha).
  2. Senha incorreta ou usuário sem permissão de acesso remoto a partir do IP onde o bot está rodando.
* **Solução:**
  1. Verifique se a URL segue estritamente o formato `mysql+aiomysql://USUARIO:SENHA@HOST:3306/BANCO?charset=utf8mb4` (sem `:` antes do `@`).
  2. Se a senha contiver símbolos especiais (`@`, `:`, `#`, `%`, `/`), converta-os para URL Encode.
  3. No servidor MySQL, conceda permissão de acesso ao usuário para o IP do bot ou para conexões remotas:
```sql
GRANT ALL PRIVILEGES ON seu_banco.* TO 'seu_usuario'@'%' IDENTIFIED BY 'sua_senha';
FLUSH PRIVILEGES;
```
     Em servidores Linux, verifique também se `bind-address = 0.0.0.0` está configurado em `/etc/mysql/mysql.conf.d/mysqld.cnf` caso o bot conecte externamente.

---

### 4. `pymysql.err.OperationalError: (1049, "Unknown database 'fusion_bot'")`

* **Erro exibido:**
  ```text
  pymysql.err.OperationalError: (1049, "Unknown database 'fusion_bot'")
  ```
* **Causa:** O Alembic e o SQLAlchemy criam apenas as tabelas, mas exigem que o banco de dados (`fusion_bot`) já exista previamente no MySQL.
* **Solução:** Crie o banco antes de rodar as migrations ou iniciar o bot:
  ```sql
  CREATE DATABASE IF NOT EXISTS fusion_bot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  ```

---

### 5. `FileNotFoundError` ao executar `alembic revision --autogenerate`

* **Erro exibido:**
  ```text
  FileNotFoundError: [Errno 2] No such file or directory: '...\database\migrations\versions\xxxx_initial_schema.py'
  ```
* **Causa:**
  1. A pasta `database/migrations/versions/` não existe no projeto recém-clonado.
  2. No Windows, o **Acesso a Pastas Controladas** do Windows Defender bloqueia o `python.exe` dentro do `venv` de criar arquivos na pasta `Documents`.
* **Solução:**
  1. Crie a pasta `database/migrations/versions` e mova o projeto para fora de `Documents` (ex.: `C:\bots\fusion_bot`).
  2. Ou inicialize todas as tabelas diretamente pelo SQLAlchemy sem depender da criação de arquivos do Alembic:
```bash
python -c "import sys, asyncio, pkgutil, importlib, database.models as m; from core.database import engine; [importlib.import_module(f'{m.__name__}.{i.name}') for i in pkgutil.iter_modules(getattr(m, '__path__', []))]; meta = next(v.metadata for mod in list(sys.modules.values()) if getattr(mod, '__name__', '').startswith('database') for v in vars(mod).values() if hasattr(v, 'metadata') and hasattr(v, '__tablename__')); ns = {'engine': engine, 'meta': meta}; exec('async def run():\n async with engine.begin() as c: await c.run_sync(meta.create_all)\n await engine.dispose()', ns); asyncio.run(ns['run']()); print('Tabelas criadas com sucesso!')"
```

---

### 6. `OSError: [Errno 10048] / [Errno 98] error while attempting to bind on address ('0.0.0.0', 8080)`

* **Erro exibido:**
  ```text
  ERROR: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8080): normalmente é permitida apenas uma utilização de cada endereço de soquete
  ```
* **Causa:** Outra instância do bot já está aberta em segundo plano ou outro serviço web (Apache, Nginx, painel local) está utilizando a porta `8080`.
* **Solução:**
  * **Windows:**
```powershell
Get-NetTCPConnection -LocalPort 8080 | Select-Object OwningProcess, @{N="App";E={(Get-Process -Id $_.OwningProcess).ProcessName}}
# Para encerrar processos Python antigos presos:
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```
  * **Linux:**
```bash
sudo lsof -i :8080
sudo systemctl restart fusion-bot
```
  * Ou altere `API_PORT=8081` no arquivo `.env`.

---

### 7. `ValueError: Fernet key must be 32 url-safe base64-encoded bytes.`

* **Erro exibido:**
  ```text
  File ".../core/security.py", line 15, in __init__
    self._fernet = Fernet(key.encode("utf-8"))
  ValueError: Fernet key must be 32 url-safe base64-encoded bytes.
  ```
* **Causa:** A variável `ENCRYPTION_KEY` no `.env` está vazia ou preenchida com uma senha/texto comum em vez de uma chave Fernet Base64 de 32 bytes. O erro ocorre quando o bot tenta criptografar a senha RCON ao adicionar um servidor CS:GO ou ao salvar o token do Mercado Pago.
* **Solução:**
  1. Gere uma chave válida:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
  2. Cole o valor gerado em `ENCRYPTION_KEY=` no `.env` e reinicie o bot.
  3. **Atenção:** Nunca altere `ENCRYPTION_KEY` após cadastrar servidores ou tokens em produção sem recadastrá-los depois, pois dados criptografados com a chave antiga gerarão `cryptography.fernet.InvalidToken`.

---

### 8. Outros Erros Comuns em Produção e Como Corrigir

| Sintoma / Log | Causa Técnica | Como Resolver |
| :--- | :--- | :--- |
| **`System.ArgumentOutOfRangeException` (PSReadLine) no PowerShell** | Bug visual do console do PowerShell ao colar blocos grandes com múltiplas quebras de linha. | Pressione `Ctrl + C` para limpar o buffer do terminal e cole os comandos em linha única. |
| **Servidor CS:GO aparece Offline ou RCON falha** | Porta bloqueada no firewall, proxy Cloudflare ativado no domínio, falta de `-usercon` no SRCDS ou IP banido por tentativas RCON. | Use o IP numérico direto (sem proxy Cloudflare), libere TCP+UDP da porta do servidor no firewall, inclua `-usercon` na inicialização e remova bloqueios no console do jogo (`removeip`). |
| **PIX é pago, mas VIP/produto não é entregue** | Webhook do Mercado Pago não consegue acessar o endpoint FastAPI (sem HTTPS ou porta bloqueada). | Configure Nginx com SSL (`https://`) apontando para `127.0.0.1:8080` e defina `PUBLIC_BASE_URL` no `.env`. |
| **`Lost connection to MySQL server during query` (Erro 2006 / 2013)** | O MySQL encerrou conexões inativas do pool após atingir `wait_timeout`. | Mantenha `pool_pre_ping=True` e `pool_recycle=1800` na engine SQLAlchemy em `core/database.py`. |
| **Botões de painéis antigos param de funcionar após reiniciar** | Views persistentes sem `timeout=None` ou não registradas no startup do bot. | Registre todas as Views persistentes via `bot.add_view(...)` no `setup_hook` antes de conectar ao Gateway. |
| **`PyNaCl / davey is not installed, voice will NOT be supported`** | Aviso informativo padrão do `discord.py` quando bibliotecas de áudio não estão instaladas. | **Não é um erro.** Como o bot não utiliza canais de voz, pode ser ignorado. |

---

## 9. Roadmap de Melhorias Técnicas (Código, Segurança e Performance)

Melhorias recomendadas para evoluir a estabilidade e a segurança do projeto em ambientes de alta disponibilidade:

### 1. Resiliência no Handler Global de Erros (`bot/events/guild_events.py`)
* **Cenário atual:** Se ocorrer uma falha de conexão com o banco durante um Slash Command, o `on_app_command_error` tenta despachar o erro para o próprio banco via `LogService.dispatch(...)`, provocando uma segunda exceção encadeada (`Task exception was never retrieved`) e deixando a interação sem resposta no Discord.
* **Melhoria:** Envolver `LogService.dispatch(...)` em um bloco `try/except Exception` com fallback para log local (`logger.exception`), garantindo que o usuário sempre receba uma resposta efêmera amigável (`interaction.response.send_message` ou `followup.send`).

### 2. Validação *Fail-Fast* na Inicialização (`main.py`)
* **Cenário atual:** Erros de configuração como `ENCRYPTION_KEY` inválida ou falha de autenticação no MySQL só são detectados quando um administrador interage com o `/painel`.
* **Melhoria:** Criar uma rotina `run_startup_checks()` antes de iniciar o bot e o Uvicorn que:
  1. Valide `get_cipher()` imediatamente no boot.
  2. Execute `SELECT 1` no `engine` para testar conectividade e credenciais do MySQL.
  3. Verifique a existência das tabelas essenciais (com opção `AUTO_CREATE_TABLES=true` para executar `Base.metadata.create_all` automaticamente).

### 3. Assinatura Criptográfica e Autenticação nos Webhooks (`api/`)
* **Cenário atual:** Rotas HTTP públicas de webhook ficam expostas na internet.
* **Melhoria:**
  * Validar o header `x-signature` (HMAC-SHA256) enviado pelo Mercado Pago usando a chave secreta da aplicação e sempre consultar o status real da transação diretamente na API do Mercado Pago antes de liberar entregas.
  * Exigir `Authorization: Bearer <SOURCEMOD_API_SECRET>` nas rotas internas consumidas pelos plugins SourceMod dos servidores CS:GO.

### 4. Cache em Memória com TTL (`modules/csgo/` e `core/`)
* **Cenário atual:** Consultas simultâneas de status de servidores disparam múltiplas requisições UDP A2S e leituras repetidas na tabela `guild_configs`.
* **Melhoria:** Adicionar cache em memória (`cachetools.TTLCache`) com TTL de 10–15 segundos para consultas A2S e 60 segundos para configurações de servidor, invalidando o cache sempre que o administrador salvar alterações no `/painel`.

### 5. Fila de Reconciliação VIP com Servidores CS:GO e Portal Web (`modules/vip/` e `tasks/scheduler.py`)
* **Cenário atual:** Caso um servidor CS:GO esteja trocando de mapa ou reiniciando no exato instante da aprovação de um pagamento PIX, o envio RCON imediato pode falhar.
* **Melhoria:** Adicionar uma flag `synced_with_gameserver` na tabela `user_vips` e criar uma tarefa periódica no `FusionScheduler` para reenviar automaticamente os comandos de ativação/remoção até receber confirmação dos servidores e do banco integrado com o portal Laravel.

### 6. Rotação de Logs e Endpoint `/health`
* **Melhoria:** Configurar `RotatingFileHandler` em `logs/fusion_bot.log` (ex.: 10 MB por arquivo, mantendo os últimos 5 backups) e expor `GET /health` no FastAPI retornando o status do Gateway Discord, latência e disponibilidade do pool MySQL para monitoramento externo (UptimeRobot / Cloudflare).

---

## 10. Execução 24/7 em Produção (Systemd + Nginx SSL)

### Serviço Systemd (`/etc/systemd/system/fusion-bot.service`)

```ini
[Unit]
Description=FUSION BOT - Discord & CS:GO Legacy Commercial System
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fusion_bot
ExecStart=/opt/fusion_bot/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Comandos para ativar e acompanhar os logs em tempo real:
```bash
sudo systemctl daemon-reload
sudo systemctl enable fusion-bot
sudo systemctl start fusion-bot
sudo journalctl -u fusion-bot -f
```

### Proxy Reverso Nginx com HTTPS (`/etc/nginx/sites-available/fusion-bot-api`)

```nginx
server {
    listen 80;
    server_name api.seudominio.com.br;

    location / {
        proxy_pass [http://127.0.0.1:8080](http://127.0.0.1:8080);
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Habilite a configuração e gere o certificado SSL gratuito com Certbot:
```bash
sudo ln -s /etc/nginx/sites-available/fusion-bot-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.seudominio.com.br
```

---
