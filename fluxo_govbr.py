#!/usr/bin/env python3
"""
Fluxo "Entrar com gov.br" da Receita, com navegador VISIVEL (headless=False).

O que faz:
  1. Abre a pagina de autorizacoes (redireciona para /login).
  2. Mapeia TODAS as requisicoes e respostas trocadas (ordem cronologica).
  3. Localiza e aciona o botao "Entrar com gov.br".
  4. Espera alguns segundos e observa:
       - se apareceu DESAFIO VISUAL do hCaptcha, ou
       - se foi direto para sso.acesso.gov.br (captcha passivo).
  5. Se chegou no SSO, extrai e decodifica o authorization_id (timestamp hex).
  6. Despeja os cookies finais (inclui HttpOnly e de terceiros).

Saida (em ./out por padrao):
  - fluxo_govbr.json   -> mapa de requisicoes/respostas + cookies + veredito
  - video/*.webm       -> gravacao da sessao (da para assistir mesmo headless)

Variaveis de ambiente:
  HEADLESS=false|true   (padrao false; em container sem tela use xvfb-run)
  WAIT_AFTER_CLICK      segundos apos o clique (padrao 5)
  START_URL             URL inicial (padrao: pagina de autorizacoes)
  CHROME_PATH           binario do Chromium (padrao: o que o Playwright instalou)
  PROXY_SERVER          proxy HTTPS opcional
  OUT_DIR               pasta de saida (padrao ./out)
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

START_URL = os.environ.get(
    "START_URL",
    "https://servicos.receitafederal.gov.br/servico/autorizacoes/minhas-autorizacoes",
)
HEADLESS = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes", "sim")
RECORD_VIDEO = os.environ.get("RECORD_VIDEO", "true").lower() in ("1", "true", "yes", "sim")
WAIT_AFTER_CLICK = int(os.environ.get("WAIT_AFTER_CLICK", "5"))
CHROME = os.environ.get("CHROME_PATH", "") or None
PROXY = os.environ.get("PROXY_SERVER", "") or None
OUT_DIR = Path(os.environ.get("OUT_DIR", str(Path(__file__).with_name("out"))))
VIDEO_DIR = OUT_DIR / "video"
OUT_JSON = OUT_DIR / "fluxo_govbr.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

# Texto/sinais do botao gov.br e do SSO alvo.
GOVBR_HINTS = ["entrar com gov.br", "gov.br", "govbr", "entrar com govbr"]
SSO_HOST = "sso.acesso.gov.br"


def decode_authorization_id(hex_id):
    """authorization_id do gov.br = timestamp do servidor (ms) em hexadecimal."""
    try:
        ms = int(hex_id, 16)
        dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
        return {"hex": hex_id, "ms": ms, "utc": dt.isoformat()}
    except Exception:
        return {"hex": hex_id, "ms": None, "utc": None}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()

    def ms():
        return round((time.monotonic() - t0) * 1000)

    result = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "start_url": START_URL,
        "headless": HEADLESS,
        "events": [],          # mapa cronologico de requisicoes/respostas
        "button": {},          # como o botao gov.br foi localizado
        "verdict": {},         # chegou no SSO? apareceu captcha visual?
        "authorization_id": None,
        "cookies_context": [],
        "cookies_cdp": [],
        "errors": [],
    }

    def add_event(kind, method, url, **extra):
        ev = {"seq": len(result["events"]), "t_ms": ms(),
              "kind": kind, "method": method, "url": url}
        ev.update(extra)
        result["events"].append(ev)

    with sync_playwright() as p:
        launch_kwargs = dict(
            executable_path=CHROME,
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        if PROXY:
            launch_kwargs["proxy"] = {"server": PROXY}
        browser = p.chromium.launch(**launch_kwargs)

        ctx_kwargs = dict(
            user_agent=UA,
            locale="pt-BR",
            viewport={"width": 1366, "height": 900},
        )
        if RECORD_VIDEO:
            ctx_kwargs["record_video_dir"] = str(VIDEO_DIR)
            ctx_kwargs["record_video_size"] = {"width": 1366, "height": 900}
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        page.on("request", lambda r: add_event(
            "request", r.method, r.url, resource_type=r.request.resource_type
            if hasattr(r, "request") else r.resource_type))
        page.on("response", lambda r: add_event(
            "response", r.request.method, r.url,
            status=r.status, resource_type=r.request.resource_type))
        page.on("requestfailed", lambda r: add_event(
            "requestfailed", r.method, r.url,
            resource_type=r.resource_type,
            failure=(r.failure or "")))

        cdp = context.new_cdp_session(page)

        # ---- 1) abre a pagina inicial ----
        try:
            nav = page.goto(START_URL, wait_until="domcontentloaded", timeout=45000)
            result["after_initial_nav"] = {
                "url": page.url, "status": nav.status if nav else None}
        except Exception as e:
            result["errors"].append(f"goto: {e}")

        # da um tempo para a pagina de login montar (hCaptcha, js)
        page.wait_for_timeout(2500)

        # ---- catalogo de elementos clicaveis (ajuda a achar o botao) ----
        try:
            candidates = page.evaluate(
                """() => {
                    const out = [];
                    const sel = 'a,button,input[type=submit],input[type=button],[role=button]';
                    for (const el of document.querySelectorAll(sel)) {
                        const txt = (el.innerText || el.value || el.alt || '').trim();
                        out.push({
                            tag: el.tagName.toLowerCase(),
                            text: txt.slice(0, 80),
                            id: el.id || null,
                            cls: (el.className && el.className.toString().slice(0,80)) || null,
                            href: el.getAttribute && el.getAttribute('href'),
                        });
                    }
                    return out;
                }""")
            result["clickable_catalog"] = candidates
        except Exception as e:
            result["errors"].append(f"catalog: {e}")
            candidates = []

        # ---- 2) localiza o botao gov.br ----
        clicked = False
        before_url = page.url
        try:
            btn = None
            # tenta por texto (case-insensitive) em links/botoes
            for hint in GOVBR_HINTS:
                loc = page.get_by_text(re.compile(hint, re.I))
                if loc.count() > 0:
                    btn = loc.first
                    result["button"] = {"strategy": "text", "hint": hint}
                    break
            # fallback: qualquer elemento cujo href/onclick aponte para o fluxo govbr
            if btn is None:
                loc = page.locator(
                    "a[href*='govbr'],button[onclick*='govbr'],"
                    "form[action*='govbr'] button,form[action*='govbr'] [type=submit]")
                if loc.count() > 0:
                    btn = loc.first
                    result["button"] = {"strategy": "attr-govbr"}

            if btn is not None:
                btn.scroll_into_view_if_needed(timeout=5000)
                btn.click(timeout=8000)
                clicked = True
            else:
                result["errors"].append("botao gov.br nao encontrado")
        except Exception as e:
            result["errors"].append(f"click: {e}")

        result["button"]["clicked"] = clicked

        # ---- 3) espera e observa o que acontece ----
        page.wait_for_timeout(WAIT_AFTER_CLICK * 1000)

        # tenta esperar explicitamente por uma navegacao ao SSO (se vier)
        reached_sso = SSO_HOST in page.url
        if not reached_sso:
            try:
                page.wait_for_url(re.compile(re.escape(SSO_HOST)), timeout=3000)
                reached_sso = True
            except Exception:
                reached_sso = SSO_HOST in page.url

        # desafio VISUAL do hCaptcha? procura iframe de challenge visivel
        captcha_visible = False
        try:
            for fr in page.frames:
                if "hcaptcha.com" in (fr.url or "") and "challenge" in (fr.url or ""):
                    captcha_visible = True
            # heuristica DOM: iframe hcaptcha com tamanho grande e visivel
            captcha_visible = captcha_visible or page.evaluate(
                """() => {
                    const ifr = [...document.querySelectorAll('iframe')]
                      .filter(f => (f.src||'').includes('hcaptcha.com'));
                    return ifr.some(f => {
                        const r = f.getBoundingClientRect();
                        const st = getComputedStyle(f);
                        return r.width > 200 && r.height > 200 &&
                               st.visibility !== 'hidden' && st.display !== 'none';
                    });
                }""")
        except Exception as e:
            result["errors"].append(f"captcha_check: {e}")

        # houve chamada getcaptcha? (sinal de execucao do hCaptcha no clique)
        getcaptcha_calls = [e for e in result["events"]
                            if "/getcaptcha/" in e["url"]]

        result["verdict"] = {
            "url_before_click": before_url,
            "final_url": page.url,
            "reached_sso": reached_sso,
            "captcha_challenge_visible": captcha_visible,
            "getcaptcha_requests": len(getcaptcha_calls),
        }

        # ---- 4) authorization_id, se chegou no SSO ----
        m = re.search(r"authorization_id=([0-9a-fA-F]+)", page.url)
        if m:
            result["authorization_id"] = decode_authorization_id(m.group(1))

        # ---- 5) cookies finais (duas vias) ----
        try:
            result["cookies_context"] = context.cookies()
        except Exception as e:
            result["errors"].append(f"context.cookies: {e}")
        try:
            result["cookies_cdp"] = cdp.send(
                "Network.getAllCookies").get("cookies", [])
        except Exception as e:
            result["errors"].append(f"cdp.getAllCookies: {e}")

        result["finished_at"] = datetime.now(timezone.utc).isoformat()

        # fecha para materializar o video
        video_path = None
        try:
            video_path = page.video.path() if page.video else None
        except Exception:
            pass
        context.close()
        browser.close()
        if video_path:
            result["video"] = str(video_path)

    OUT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    # ---- resumo no stdout ----
    v = result["verdict"]
    print(f"Saida: {OUT_JSON}")
    print(f"Eventos (req+resp): {len(result['events'])}")
    print(f"Botao gov.br: {result['button']}")
    print(f"URL final: {v.get('final_url')}")
    print(f"Chegou no SSO? {v.get('reached_sso')}")
    print(f"Captcha visual apareceu? {v.get('captcha_challenge_visible')}")
    print(f"Chamadas getcaptcha: {v.get('getcaptcha_requests')}")
    if result["authorization_id"]:
        a = result["authorization_id"]
        print(f"authorization_id: {a['hex']} -> {a['utc']} (UTC)")
    print(f"Cookies: contexto={len(result['cookies_context'])} "
          f"cdp={len(result['cookies_cdp'])}")
    if result.get("video"):
        print(f"Video: {result['video']}")
    if result["errors"]:
        print("ERROS:", result["errors"])


if __name__ == "__main__":
    main()
