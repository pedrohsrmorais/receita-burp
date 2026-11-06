#!/usr/bin/env python3
"""
Acessa a pagina de autorizacoes da Receita, espera alguns segundos e despeja
TODOS os cookies (inclusive HttpOnly e de terceiros) em JSON.

Roda 100% headless. Pega os cookies por duas vias:
  1) context.cookies()            -> cookies do contexto (ja inclui HttpOnly)
  2) CDP Network.getAllCookies    -> cookie jar inteiro do contexto (todos os
                                     dominios: sso.acesso.gov.br, *.hcaptcha.com...)

Uso:
    python3 receita_cookies.py
Saida:
    receita_cookies.json  (no mesmo diretorio do script)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://servicos.receitafederal.gov.br/servico/autorizacoes/minhas-autorizacoes"
# Caminho do Chromium. Vazio (padrao) = deixa o Playwright usar o browser que
# ele instalou (caso do container oficial do Playwright e de `playwright install`).
# Defina CHROME_PATH para apontar um binario especifico.
CHROME = os.environ.get("CHROME_PATH", "") or None
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "3"))
OUT = Path(os.environ.get("OUT_PATH") or Path(__file__).with_name("receita_cookies.json"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def main():
    result = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target_url": URL,
        "navigation": {},
        "redirect_chain": [],
        "responses": [],       # toda resposta vista durante o carregamento
        "cookies_context": [], # via Playwright context.cookies()
        "cookies_cdp": [],     # via CDP Storage.getCookies (jar completo)
        "errors": [],
    }

    with sync_playwright() as p:
        launch_kwargs = dict(
            executable_path=CHROME,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        # Neste ambiente a saida HTTPS passa por um proxy local. Fora dele,
        # defina PROXY_SERVER="" ou remova este bloco.
        proxy_server = os.environ.get("PROXY_SERVER", os.environ.get("HTTPS_PROXY", ""))
        if proxy_server:
            launch_kwargs["proxy"] = {"server": proxy_server}
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=UA,
            locale="pt-BR",
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()

        # Log de todas as respostas (ajuda a ver redirects e subrecursos)
        def on_response(resp):
            try:
                result["responses"].append({
                    "url": resp.url,
                    "status": resp.status,
                    "resource_type": resp.request.resource_type,
                })
            except Exception as e:
                result["errors"].append(f"on_response: {e}")

        page.on("response", on_response)

        # Sessao CDP para pegar o jar completo depois
        cdp = context.new_cdp_session(page)

        try:
            nav = page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            if nav is not None:
                result["navigation"] = {
                    "final_url": page.url,
                    "status": nav.status,
                    "ok": nav.ok,
                }
                # cadeia de redirecionamentos do documento principal
                req = nav.request
                chain = []
                while req is not None:
                    chain.append(req.url)
                    req = req.redirected_from
                result["redirect_chain"] = list(reversed(chain))
        except Exception as e:
            result["errors"].append(f"goto: {e}")

        # Espera para dar tempo de hCaptcha/anti-bot rodarem e setarem cookies
        time.sleep(WAIT_SECONDS)

        # 1) Cookies do contexto (sem filtro de URL = todos do contexto)
        try:
            result["cookies_context"] = context.cookies()
        except Exception as e:
            result["errors"].append(f"context.cookies: {e}")

        # 2) Cookie jar completo via CDP. Usa Network.getAllCookies (opera sobre
        #    o contexto da sessao/aba). Storage.getCookies sem browserContextId
        #    leria o contexto PADRAO do browser, nao o contexto isolado que o
        #    Playwright cria -> voltaria vazio.
        try:
            jar = cdp.send("Network.getAllCookies")
            result["cookies_cdp"] = jar.get("cookies", [])
        except Exception as e:
            result["errors"].append(f"cdp.getAllCookies: {e}")

        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        context.close()
        browser.close()

    # Resumo por dominio para leitura rapida
    by_domain = {}
    for c in result["cookies_cdp"]:
        d = c.get("domain", "?")
        by_domain.setdefault(d, []).append({
            "name": c.get("name"),
            "httpOnly": c.get("httpOnly"),
            "secure": c.get("secure"),
            "sameSite": c.get("sameSite"),
            "session": c.get("session"),
        })
    result["summary_by_domain"] = by_domain

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # Resumo no stdout
    print(f"Saida: {OUT}")
    print(f"Final URL: {result['navigation'].get('final_url')}")
    print(f"Status navegacao principal: {result['navigation'].get('status')}")
    print(f"Redirects: {len(result['redirect_chain'])}  Respostas: {len(result['responses'])}")
    print(f"Cookies (contexto): {len(result['cookies_context'])}  "
          f"Cookies (CDP jar): {len(result['cookies_cdp'])}")
    print("Por dominio:")
    for d, cs in sorted(by_domain.items()):
        names = ", ".join(x["name"] for x in cs)
        print(f"  {d}: {names}")
    if result["errors"]:
        print("ERROS:", result["errors"], file=sys.stderr)


if __name__ == "__main__":
    main()
