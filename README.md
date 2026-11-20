# FUSION BOT — Sistema Comercial de Gerenciamento Discord & CS:GO Legacy

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x%20Async-D71F00?style=for-the-badge)
![MySQL](https://img.shields.io/badge/MySQL-8.0%2B%20%7C%20MariaDB-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![Mercado Pago](https://img.shields.io/badge/Mercado%20Pago-PIX%20Oficial-00B1EA?style=for-the-badge)

Sistema completo e modular de automação comercial, atendimento e gerenciamento de servidores **Counter-Strike: Global Offensive (Legacy)** via Discord. Desenvolvido **100% em Python assíncrono**, integrando **discord.py 2.x**, **FastAPI**, **SQLAlchemy 2.x**, **Alembic**, **Mercado Pago PIX Oficial**, **APScheduler**, **Criptografia Fernet** e **Protocolo Valve A2S + RCON + SourceMod REST** para integração total com os servidores e o portal web **FUSION**.

---

## Sumário

1. [Arquitetura e Módulos do Sistema](#1-arquitetura-e-módulos-do-sistema)
2. [Estrutura de Diretórios](#2-estrutura-de-diretórios)
3. [Pré-requisitos](#3-pré-requisitos)
4. [Instalação Completa (Linux VPS e Windows)](#4-instalação-completa-linux-vps-e-windows)
5. [Configuração das Variáveis de Ambiente (.env)](#5-configuração-das-variáveis-de-ambiente-env)
6. [Preparação do Banco de Dados e Tabelas](#6-preparação-do-banco-de-dados-e-tabelas)
7. [Configuração no Discord Developer Portal](#7-configuração-no-discord-developer-portal)
8. [Integração CS:GO Legacy (A2S, RCON e SourceMod)](#8-integração-csgo-legacy-a2s-rcon-e-sourcemod)
9. [Integração Mercado Pago (PIX Automático e Webhooks)](#9-integração-mercado-pago-pix-automático-e-webhooks)
10. [Guia Completo de Erros Conhecidos, Possíveis Falhas e Soluções](#10-guia-completo-de-erros-conhecidos-possíveis-falhas-e-soluções)
11. [Roadmap de Melhorias Recomendadas (Código, Segurança e Infraestrutura)](#11-roadmap-de-melhorias-recomendadas-código-segurança-e-infraestrutura)
12. [Execução 24/7 em Produção (Systemd + Nginx SSL)](#12-execução-247-em-produção-systemd--nginx-ssl)

---

## 1. Arquitetura e Módulos do Sistema

O **FUSION BOT** opera em arquitetura híbrida orientada a eventos, executando simultaneamente no mesmo *event loop* assíncrono (`asyncio.gather`):

* **Cliente Discord (`discord.py 2.x`)**: Gerencia Slash Commands (`/painel` e comandos de usuário), Views persistentes, Modais interativos e roteamento de eventos.
* **Servidor HTTP/REST (`FastAPI + Uvicorn`)**: Recebe notificações de pagamento em tempo real (Webhooks do Mercado Pago) e eventos/consultas dos plugins SourceMod nos servidores CS:GO.
* **Agendador de Tarefas (`APScheduler`)**: Executa rotinas periódicas a cada 1 minuto (`FusionScheduler`), liberando estoque de pedidos expirados, expirando VIPs vencidos e sincronizando estados.
* **Camada de Persistência Assíncrona (`SQLAlchemy 2.x + aiomysql`)**: Pool de conexões assíncrono com suporte a MySQL 8.0+, MariaDB e SQLite (para testes locais).

### Módulos Principais

| Módulo | Caminho | Responsabilidades |
| :--- | :--- | :--- |
| **Painel Administrativo** | `bot/cogs/admin_cog.py`<br>`bot/views/panel_views.py`<br>`bot/modals/panel_modals.py` | Central unificada via `/painel` para configurar canais de log, permissões por módulo, credenciais Mercado Pago, produtos, planos VIP, tickets e servidores CS:GO. |
| **Vendas & Checkout PIX** | `modules/sales/` | Carrinho de compras, cupons de desconto, geração de QR Code / Copia e Cola PIX via API oficial, reserva temporária de estoque digital e entrega automática na DM/ticket. |
| **Sistema VIP Integrado** | `modules/vip/` | Planos VIP com duração configurável, atribuição automática de cargos no Discord e ativação/remoção automática nos servidores CS:GO via SteamID. |
| **CS:GO Legacy & RCON** | `modules/csgo/` | Consulta em tempo real via Valve A2S (mapa, jogadores online, ping), execução remota de comandos RCON com senha criptografada, vinculação Discord ↔ SteamID e ranking/histórico de partidas. |
| **Tickets Multidepartamento** | `modules/tickets/` | Painéis de abertura por departamento, reivindicação (*claim*) por staff, transcrição completa de mensagens e sistema de avaliação de atendimento. |
| **Logs & Auditoria** | `modules/logs/`<br>`bot/events/guild_events.py` | Interceptação global de erros de comandos e registro detalhado de vendas, ações administrativas, RCON e tickets em canais dedicados. |

---

## 2. Estrutura de Diretórios

```text
fusion_bot/
├── main.py                      # Ponto de entrada: inicia Discord Bot + FastAPI + Scheduler
├── requirements.txt             # Dependências Python do projeto
├── .env                         # Variáveis de ambiente (NÃO commitar no Git!)
├── .env.example                 # Modelo de configuração de ambiente
├── alembic.ini                  # Configuração do Alembic para migrations
│
├── api/                         # Rotas FastAPI (Webhooks Mercado Pago, SourceMod REST, Healthcheck)
├── bot/
│   ├── cogs/
│   │   └── admin_cog.py         # Slash command /painel e comandos administrativos
│   ├── events/
│   │   └── guild_events.py      # Listeners de servidor e tratamento global de erros
│   ├── modals/
│   │   └── panel_modals.py      # Modais de cadastro (Servidor CS:GO, VIP, Produtos, MP)
│   └── views/
│       └── panel_views.py       # Interfaces interativas, botões e seletores do painel
│
├── core/
│   ├── config.py                # Carregamento e validação de configurações (.env)
│   ├── database.py              # Engine assíncrona SQLAlchemy e gerenciamento de sessões
│   └── security.py              # Criptografia simétrica Fernet (senhas RCON e tokens)
│
├── database/
│   ├── models/                  # Modelos ORM (tabelas do banco de dados)
│   └── migrations/              # Scripts de migração Alembic
│       ├── env.py
│       └── versions/
│
├── modules/
│   ├── csgo/                    # Integração A2S, RCON, Steam Link e estatísticas
│   ├── logs/                    # Despacho de logs estruturados para o Discord
│   ├── sales/                   # Loja, carrinho, pedidos, cupons e Mercado Pago
│   ├── tickets/                 # Atendimento, transcrições e avaliações
│   └── vip/                     # Gestão de assinaturas VIP Discord + CS:GO
│
└── tasks/
    └── scheduler.py             # Rotinas agendadas (expiração de pedidos e VIPs)