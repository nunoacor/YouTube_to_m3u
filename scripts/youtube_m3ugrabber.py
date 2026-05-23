#!/usr/bin/env python3
# coding: utf-8
"""
youtube_m3ugrabber.py (VERSÃO CORRIGIDA)
Gera um .m3u8 por canal em ./canais (ou em --outdir).
NÃO escreve cookies nos .m3u8. Se existirem cookies na env, são usados
somente num ficheiro temporário para o yt-dlp e eliminados no fim.

Também faz limpeza de quaisquer linhas "#EXTVLCOPT:http-cookie=" em .m3u8 já existentes.
"""
from __future__ import annotations
import os
import sys
import argparse
import logging
import tempfile
import stat
import re
import shlex
import subprocess
from typing import Optional, Dict, Any, List

FALLBACK_M3U = "https://raw.githubusercontent.com/nunoacor/YouTube_to_m3u/main/assets/offline.m3u"

try:
    from yt_dlp import YoutubeDL
except Exception:
    YoutubeDL = None  # fallback para usar o binário yt-dlp


def write_temp_cookies(cookies_text: str) -> Optional[str]:
    if not cookies_text:
        return None
    tf = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
    try:
        tf.write(cookies_text)
        tf.flush()
        tf.close()
        try:
            os.chmod(tf.name, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        return tf.name
    except Exception:
        try:
            tf.close()
            os.unlink(tf.name)
        except Exception:
            pass
        return None


def remove_file_silent(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except Exception:
        pass


def parse_height(fmt: Dict[str, Any]) -> int:
    h = fmt.get("height")
    if isinstance(h, int):
        return h or 0
    res = fmt.get("resolution") or fmt.get("format_note") or fmt.get("format") or ""
    if isinstance(res, str):
        m = re.search(r'(\d{2,4})x(\d{2,4})', res)
        if m:
            try:
                return int(m.group(2))
            except Exception:
                pass
        m2 = re.search(r'(\d{2,4})p', res)
        if m2:
            try:
                return int(m2.group(1))
            except Exception:
                pass
    return 0


def is_hls_format(fmt: Dict[str, Any]) -> bool:
    proto = (fmt.get("protocol") or "").lower()
    ext = (fmt.get("ext") or "").lower()
    url = (fmt.get("url") or "").lower()
    if "m3u8" in proto or ext == "m3u8" or ".m3u8" in url:
        return True
    if "m3u8" in proto or "hls" in proto:
        return True
    return False


def choose_best_stream_url(info: Dict[str, Any]) -> str:
    formats: List[Dict[str, Any]] = info.get("formats") or []
    if not formats:
        if "url" in info and info.get("url"):
            return info.get("url")
        return FALLBACK_M3U

    entries = []
    for f in formats:
        height = parse_height(f)
        entries.append((height, f))
    entries.sort(key=lambda x: x[0], reverse=True)

    for height, f in entries:
        if is_hls_format(f):
            url = f.get("url")
            if url:
                logging.debug("Escolhido HLS: %sp -> %s", height, url)
                return url

    for height, f in entries:
        url = f.get("url")
        if url:
            logging.debug("Escolhido melhor formato: %sp -> %s", height, url)
            return url

    if "url" in info and info.get("url"):
        return info.get("url")
    return FALLBACK_M3U


def yt_dlp_get_direct_url_cli(url: str, cookiefile: Optional[str] = None, timeout: int = 15) -> Optional[str]:
    cmd = ["yt-dlp", "-g", "--geo-bypass", "--geo-bypass-country", "PT"]
    if cookiefile:
        cmd += ["--cookies", cookiefile]
    cmd += [url]
    try:
        logging.debug("Executando: %s", " ".join(shlex.quote(x) for x in cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            logging.debug("yt-dlp -g devolveu código %s; stderr: %s", res.returncode, res.stderr.strip())
            return None
        out = res.stdout.strip()
        if not out:
            return None
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        for ln in lines:
            if ".m3u8" in ln:
                return ln
        return lines[0]
    except Exception as e:
        logging.debug("Erro ao correr yt-dlp -g: %s", e)
        return None


def extract_stream_with_yt_dlp(url: str, cookiefile: Optional[str] = None, timeout: int = 15) -> str:
    direct = yt_dlp_get_direct_url_cli(url, cookiefile=cookiefile, timeout=timeout)
    if direct:
        return direct

    if YoutubeDL is not None:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "dump_single_json": True,
            "socket_timeout": timeout,
            "geo_bypass": True,
            "geo_bypass_country": "PT",
            "xff": "PT",
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        }
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            return choose_best_stream_url(info)
        except Exception as e:
            logging.debug("API yt-dlp falhou: %s", e)

    return FALLBACK_M3U


def sanitize_filename(name: str, max_len: int = 200) -> str:
    if not name:
        name = "canal"
    name = "".join(ch for ch in name if ord(ch) >= 32)
    name = re.sub(r'[\\/:\*\?"<>\|]+', "_", name)
    name = re.sub(r'[^0-9A-Za-zÀ-ž \-\._]', '_', name)
    name = name.strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    if not name:
        name = "canal"
    return name


def strip_cookie_lines_from_m3u8(outdir: str) -> None:
    """
    Procura .m3u8 em outdir e remove linhas começadas por '#EXTVLCOPT:http-cookie='.
    Isso limpa qualquer exposição anterior.
    """
    if not os.path.isdir(outdir):
        return
    for entry in os.listdir(outdir):
        path = os.path.join(outdir, entry)
        if not os.path.isfile(path):
            continue
        if not entry.lower().endswith(".m3u8"):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            changed = False
            new_lines = []
            for ln in lines:
                if ln.strip().lower().startswith("#extvlcopt:http-cookie="):
                    changed = True
                    continue
                new_lines.append(ln)
            if changed:
                # escrever de volta (atomicidade simples)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                os.replace(tmp, path)
                logging.info("Linha(s) de cookie removidas em: %s", path)
        except Exception as e:
            logging.debug("Erro ao limpar %s : %s", path, e)


def write_m3u8_file(outdir: str, channel_name: str, group_title: Optional[str], tvg_logo: Optional[str],
                    tvg_id: Optional[str], stream_url: str) -> str:
    """
    NÃO escreve cookies. Só escreve UA/referrer (não sensíveis).
    """
    fname = sanitize_filename(channel_name) + ".m3u8"
    path = os.path.join(outdir, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write('#EXTM3U\n')
            gi = (group_title or "").replace('"', "'")
            lg = (tvg_logo or "").replace('"', "'")
            tid = (tvg_id or "").replace('"', "'")
            f.write(f'#EXTINF:-1 group-title="{gi}" tvg-logo="{lg}" tvg-id="{tid}",{channel_name}\n')
            # escrever opções não sensíveis (User-Agent e Referer) que ajudam o VLC
            f.write('#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)\n')
            f.write('#EXTVLCOPT:http-referrer=https://www.youtube.com/\n')
            # NÃO escrever cookies aqui!
            f.write(stream_url.strip() + "\n")
        logging.info("Criado: %s", path)
    except Exception as e:
        logging.error("Falha a criar %s : %s", path, e)
    return path


def process_file(infile: str, outdir: str, cookiefile: Optional[str], timeout: int) -> None:
    if not os.path.exists(infile):
        raise FileNotFoundError(infile)

    os.makedirs(outdir, exist_ok=True)

    # Limpar ficheiros já existentes para remover eventuais cookies embutidos
    strip_cookie_lines_from_m3u8(outdir)

    with open(infile, "r", encoding="utf-8", errors="ignore") as f:
        lines = [ln.rstrip("\n") for ln in f]

    pending_meta = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("~~"):
            continue

        if "|" in line and not line.lower().startswith("http"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                ch_name = parts[0]
                grp_title = parts[1]
                tvg_logo = parts[2]
                tvg_id = parts[3]
                pending_meta = {
                    "ch_name": ch_name,
                    "grp_title": grp_title,
                    "tvg_logo": tvg_logo,
                    "tvg_id": tvg_id,
                }
                continue
            else:
                pending_meta = None
                continue

        if line.lower().startswith("http"):
            logging.info("Processando URL: %s", line)
            stream = extract_stream_with_yt_dlp(line, cookiefile=cookiefile, timeout=timeout)

            if pending_meta:
                ch_name = pending_meta.get("ch_name") or line
                grp_title = pending_meta.get("grp_title")
                tvg_logo = pending_meta.get("tvg_logo")
                tvg_id = pending_meta.get("tvg_id")
                pending_meta = None
            else:
                ch_name = re.sub(r'https?://', '', line)
                ch_name = ch_name.split('/')[0] + "_" + ch_name.split('/')[-1][:40]
                grp_title = ""
                tvg_logo = ""
                tvg_id = ""

            write_m3u8_file(outdir, ch_name, grp_title, tvg_logo, tvg_id, stream)
            continue

        pending_meta = None
        continue

    # limpar de novo caso algo tenha ficado
    strip_cookie_lines_from_m3u8(outdir)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gerar .m3u8 por canal (NÃO expõe cookies).")
    parser.add_argument("-i", "--input", default="../youtube_channel_info.txt", help="Ficheiro de input (default ../youtube_channel_info.txt)")
    parser.add_argument("-o", "--outdir", default="canais", help="Pasta de output (default ./canais)")
    parser.add_argument("--timeout", type=int, default=15, help="Timeout para yt-dlp (segundos)")
    parser.add_argument("--debug", action="store_true", help="Ativa logging debug")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="[%(levelname)s] %(message)s")

    cookies_text = os.environ.get("YT_COOKIES", "") or os.environ.get("YOUTUBE_COOKIES", "")
    cookiefile_path = None
    try:
        if cookies_text:
            cookiefile_path = write_temp_cookies(cookies_text)
            logging.debug("Ficheiro de cookies temporário criado (USADO APENAS PELO YT-DLP): %s", cookiefile_path)
            logging.info("NOTA: os cookies não serão escritos nos ficheiros .m3u8")
        else:
            logging.debug("Sem cookies fornecidos; a correr sem autenticação.")

        process_file(args.input, args.outdir, cookiefile_path, args.timeout)
        logging.info("Processamento concluído. Ficheiros em: %s", os.path.abspath(args.outdir))
    finally:
        if cookiefile_path:
            remove_file_silent(cookiefile_path)
            logging.debug("Ficheiro de cookies temporário removido.")


if __name__ == "__main__":
    main()
