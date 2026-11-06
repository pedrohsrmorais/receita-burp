# receita-burp

Análise do fluxo de autenticação gov.br / Receita Federal a partir de capturas
do Burp Suite, e automação de apoio.

## `receita_cookies.py`

Acessa `https://servicos.receitafederal.gov.br/servico/autorizacoes/minhas-autorizacoes`
com Playwright (Chromium headless), espera alguns segundos e despeja **todos** os
cookies — inclusive HttpOnly e de terceiros (`sso.acesso.gov.br`, `*.hcaptcha.com`) —
em `receita_cookies.json`.

Os cookies são coletados por duas vias, para conferência:

1. `context.cookies()` — cookies do contexto (já inclui HttpOnly).
2. CDP `Storage.getCookies` — o cookie jar inteiro do navegador.

O JSON de saída traz ainda: URL final, status, cadeia de redirects, todas as
respostas vistas no carregamento e um resumo por domínio.

### Uso

```bash
pip install playwright
playwright install chromium
python3 receita_cookies.py
```

### Proxy

Se a saída HTTPS passar por um proxy, defina `PROXY_SERVER` (ou `HTTPS_PROXY`)
antes de rodar. Sem proxy, deixe as variáveis em branco. O `executable_path`
fixo no script é específico de um ambiente; na sua máquina, deixe o Playwright
localizar o Chromium instalado por `playwright install`.

## Notas da análise

- O `authorization_id` do SSO (`sso.acesso.gov.br`) é o relógio do servidor em
  milissegundos, escrito em hexadecimal. Ex.: `0x1a1b237b397` = 1793991357335 ms
  = 06/11/2026 15:55:57 BRT, batendo com o cabeçalho `Date` da resposta.
- O hCaptcha roda em modo invisível: aprovado por score de risco, sem desafio
  visual quando a sessão parece legítima.
