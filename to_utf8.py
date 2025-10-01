#!/usr/bin/env python3
import os, re, sys, subprocess, shutil, pathlib, argparse, time

EXT_DEFAULT = {".html", ".htm", ".css", ".js"}

ENC_MAP = {
    "UTF-8": "UTF-8", "UTF8": "UTF-8", "US-ASCII": "UTF-8", "ASCII": "UTF-8",
    "WINDOWS-1252": "CP1252", "CP-1252": "CP1252", "CP1252": "CP1252",
    "WINDOWS-1250": "CP1250", "CP-1250": "CP1250", "CP1250": "CP1250",
    "ISO-8859-1": "ISO-8859-1", "ISO8859-1": "ISO-8859-1",
    "ISO-8859-2": "ISO-8859-2", "ISO8859-2": "ISO-8859-2",
    "ISO-8859-3": "ISO-8859-3", "ISO8859-3": "ISO-8859-3",
    "ISO-8859-9": "ISO-8859-9", "ISO8859-9": "ISO-8859-9",
    "ISO-8859-13": "ISO-8859-13", "ISO8859-13": "ISO-8859-13",
    "ISO-8859-15": "ISO-8859-15", "ISO8859-15": "ISO-8859-15",
    "IBM865": "CP865", "CP865": "CP865",
    "MAC-CENTRALEUROPE": "MAC-CENTRALEUROPE", "MACCENTRALEUROPE": "MAC-CENTRALEUROPE",
}

META_CHARSET_RE = re.compile(
    rb'<meta[^>]*charset\s*=\s*["\']?\s*([\-A-Za-z0-9_]+)[^>]*>',
    re.IGNORECASE | re.DOTALL,
)
HTTP_EQUIV_RE = re.compile(
    rb'<meta\s+http-equiv\s*=\s*["\']?content-type["\']?\s+content\s*=\s*["\']\s*text/html\s*;\s*charset\s*=\s*([\-A-Za-z0-9_]+)\s*["\']\s*>',
    re.IGNORECASE | re.DOTALL,
)

def p(msg: str):
    print(msg, flush=True)

def is_valid_utf8(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False

def which(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def uchardet_guess(path: pathlib.Path) -> str:
    if not which("uchardet"):
        return ""
    try:
        out = subprocess.check_output(["uchardet", str(path)], stderr=subprocess.DEVNULL, timeout=3)
        return out.decode("ascii", "ignore").strip()
    except Exception:
        return ""

def normalize(enc: str) -> str:
    if not enc:
        return ""
    enc = enc.strip().upper().replace(" ", "")
    return ENC_MAP.get(enc, enc)

def declared_charset(b: bytes) -> str:
    m = META_CHARSET_RE.search(b)
    if m:
        return m.group(1).decode("ascii", "ignore")
    m = HTTP_EQUIV_RE.search(b)
    if m:
        return m.group(1).decode("ascii", "ignore")
    return ""

def ensure_meta_utf8(text: str, is_html: bool) -> str:
    if not is_html:
        return text
    text = re.sub(
        r'(?is)<meta[^>]*charset\s*=\s*["\']?[-A-Za-z0-9_]+[^>]*>',
        '<meta charset="utf-8">', text
    )
    text = re.sub(
        r'(?is)<meta\s+http-equiv\s*=\s*["\']?content-type["\']?\s+content\s*=\s*["\']text/html;\s*charset\s*=\s*[-A-Za-z0-9_]+["\']\s*>',
        '<meta charset="utf-8">', text
    )
    if re.search(r'(?is)<meta[^>]*charset\s*=\s*"utf-8"', text) is None:
        text = re.sub(r'(?is)<head([^>]*)>',
                      r'<head\1>\n<meta charset="utf-8">', text, count=1)
    return text

def convert_with_iconv(path: pathlib.Path, enc: str, timeout_s: float) -> bytes:
    try:
        proc = subprocess.run(
            ["iconv", "-f", enc, "-t", "UTF-8", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode == 0 and is_valid_utf8(proc.stdout):
            return proc.stdout
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return b""

def process_file(path: pathlib.Path, timeout_s: float, dry_run: bool=False) -> str:
    is_html = path.suffix.lower() in {".html", ".htm"}
    data = path.read_bytes()

    dec = declared_charset(data)
    det = uchardet_guess(path)

    # Already UTF-8 and not declaring legacy → normalize meta & skip
    if is_valid_utf8(data) and (not dec or dec.upper() in ("UTF-8","UTF8","US-ASCII","ASCII")):
        if not dry_run:
            if is_html:
                text = ensure_meta_utf8(data.decode("utf-8"), is_html=True)
                path.write_text(text, encoding="utf-8")
        return f"SKIP  {path} (already UTF-8)"

    # Candidate encodings
    candidates = [dec, det, "CP1252", "ISO-8859-1", "CP1250", "ISO-8859-13", "CP865", "MAC-CENTRALEUROPE"]
    seen, ordered = set(), []
    for c in candidates:
        n = normalize(c) if c else ""
        if n and n not in seen:
            ordered.append(n); seen.add(n)

    for enc in ordered:
        out = convert_with_iconv(path, enc, timeout_s)
        if not out:
            continue
        if dry_run:
            return f"OK-DRY {path} (source: {enc})"
        try:
            text = out.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if is_html:
            text = ensure_meta_utf8(text, is_html=True)
        path.write_text(text, encoding="utf-8")
        return f"OK    {path} (source: {enc})"

    # Last-resort latin-1 transcode (preserves bytes visually): mark to review
    try:
        text = data.decode("latin-1")
        if is_html:
            text = ensure_meta_utf8(text, is_html=True)
        if not dry_run:
            path.write_text(text, encoding="utf-8")
        return f"OK*   {path} (fallback latin-1 – check visually)"
    except Exception:
        return f"FAIL  {path} (declared={dec or '-'}; detected={det or '-'})"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext", default="html,htm,css,js", help="comma list of extensions")
    ap.add_argument("--timeout", type=float, default=5.0, help="per-file iconv timeout (seconds)")
    ap.add_argument("--dry-run", action="store_true", help="don’t modify files; just report")
    ap.add_argument("--root", default=".", help="root folder")
    ap.add_argument("--only-html", action="store_true", help="process only .html/.htm")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    exts = {("." + e.strip().lstrip(".").lower()) for e in args.ext.split(",") if e.strip()}
    if args.only_html:
        exts = {".html", ".htm"}

    files = []
    for dp, _, fns in os.walk(root):
        for name in fns:
            if pathlib.Path(name).suffix.lower() in exts:
                files.append(pathlib.Path(dp) / name)
    files.sort()

    total = len(files)
    p(f"Found {total} files with extensions {sorted(exts)} in {root}")
    start = time.time()
    for i, f in enumerate(files, 1):
        p(f"[{i}/{total}] {f}")
        try:
            msg = process_file(f, timeout_s=args.timeout, dry_run=args.dry_run)
            p("  " + msg)
        except KeyboardInterrupt:
            p("\nInterrupted by user.")
            sys.exit(1)
        except Exception as e:
            p(f"  ERROR {f}: {e}")

    p(f"Done in {time.time()-start:.1f}s.")

if __name__ == "__main__":
    main()
