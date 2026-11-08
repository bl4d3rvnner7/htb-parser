import os
import re
import sys
import html as htmllib
import base64
import hashlib
import argparse
import subprocess
import requests
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from markdown import markdown
from pygments.formatters import HtmlFormatter
from urllib.parse import urljoin, urldefrag, urlsplit, unquote, quote

# Install required: pip install requests beautifulsoup4 markdown pygments
# === BANNER ===


BANNER = """\x1b[1;34m
██╗  ██╗████████╗██████╗     ██████╗  █████╗ ██████╗ ███████╗███████╗██████╗ 
██║  ██║╚══██╔══╝██╔══██╗    ██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔════╝██╔══██╗
███████║   ██║   ██████╔╝    ██████╔╝███████║██████╔╝███████╗█████╗  ██████╔╝
██╔══██║   ██║   ██╔══██╗    ██╔═══╝ ██╔══██║██╔══██╗╚════██║██╔══╝  ██╔══██╗
██║  ██║   ██║   ██████╔╝    ██║     ██║  ██║██║  ██║███████║███████╗██║  ██║
╚═╝  ╚═╝   ╚═╝   ╚═════╝     ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝
                                                                             
                        \x1b[1;35m@viperzcrew2 on Telegram\x1b[0m
"""
# === ARGPARSE ===
parser = argparse.ArgumentParser(description='Generate offline HTB module')
target = parser.add_mutually_exclusive_group(required=True)
target.add_argument('--module', type=int, help='Download a single module by ID')
target.add_argument('--path', type=int, help='Download EVERY module of a job-role/skill path by ID (e.g. 405)')
parser.add_argument('--out', default='.', help='Output directory (default: current directory)')
parser.add_argument('--resume', action='store_true',
                    help='With --path: skip modules that were already downloaded (default: re-download everything)')
parser.add_argument('--force', action='store_true', help=argparse.SUPPRESS)  # kept for old command lines; now the default
parser.add_argument('--order', type=int, help=argparse.SUPPRESS)  # internal: position inside a path
parser.add_argument('--quiet', action='store_true', help=argparse.SUPPRESS)  # internal: no banner
parser.add_argument('--cookie', required=False, help='Cookie string (optional; if not provided, read from cookie.txt)')
parser.add_argument('--inline', action='store_true',
                    help='Embed images into the HTML as base64 data URIs (each page becomes fully self-contained)')
parser.add_argument('--workers', type=int, default=8, help='Parallel image downloads (default: 8)')
parser.add_argument('--host', default='https://academy.hackthebox.com', help=argparse.SUPPRESS)
parser.add_argument('--cdn', default='https://cdn.services-k8s.prod.aws.htb.systems', help=argparse.SUPPRESS)
args = parser.parse_args()
if not args.quiet:
    print(BANNER)

# === CONFIG ===
MODULE_ID = args.module
HOST = args.host.rstrip('/')
HOSTNAME = urlsplit(HOST).hostname
CDN = args.cdn.rstrip('/')
BASE_API = f"{HOST}/api/v2"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
    'Accept': 'application/json',
    'Referer': f'{HOST}/beta/module/{MODULE_ID}',
}

if args.cookie:
    cookie_str = args.cookie.strip()
elif os.environ.get("HTB_COOKIE"):
    cookie_str = os.environ["HTB_COOKIE"].strip()
elif os.path.exists("cookie.txt"):
    with open("cookie.txt", "r") as f:
        cookie_str = f.read().strip()
else:
    raise ValueError("No --cookie provided and cookie.txt not found.")

# One shared session: connection pooling + automatic retries on 429/5xx.
# The cookies live in the session's cookie jar and are scoped to the Academy
# host only. (A hand-written "Cookie" header would be silently dropped by
# `requests` on any redirect, and would leak to third-party image hosts.)
session = requests.Session()
_retry = Retry(total=4, backoff_factor=0.8, status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=("GET",), respect_retry_after_header=True)
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=16, pool_maxsize=16)
session.mount("https://", _adapter)
session.mount("http://", _adapter)
for part in re.split(r';\s*', cookie_str.removeprefix('Cookie:').strip()):
    if '=' in part:
        k, v = part.split('=', 1)
        session.cookies.set(k.strip(), v.strip(), domain=HOSTNAME, path='/')


class ApiError(Exception):
    """Raised (never sys.exit'd) so callers that can recover - e.g. the per-section
    loop, which should skip one bad section instead of aborting the whole module -
    get a normal exception instead of an uncatchable SystemExit."""
    pass


def api_get(url):
    try:
        r = session.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        raise ApiError(f"Request failed for {url}: {type(e).__name__}") from e
    if r.status_code in (401, 403) or 'json' not in r.headers.get('Content-Type', ''):
        raise ApiError(f"API request failed ({r.status_code}) for {url} - cookie expired or invalid?")
    r.raise_for_status()
    return r.json()


def api_get_or_exit(url):
    """For call sites with no fallback (there's nothing to build without this response)."""
    try:
        return api_get(url)
    except ApiError as e:
        sys.exit(f"\x1b[1;31m{e}\x1b[0m")


# === IMAGE HANDLING ===
IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico', '.avif'}
MIME_BY_EXT = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
               '.webp': 'image/webp', '.svg': 'image/svg+xml', '.bmp': 'image/bmp',
               '.ico': 'image/x-icon', '.avif': 'image/avif'}
EXT_BY_MIME = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp',
               'image/svg+xml': '.svg', 'image/bmp': '.bmp', 'image/x-icon': '.ico',
               'image/vnd.microsoft.icon': '.ico', 'image/avif': '.avif'}
LAZY_ATTRS = ('src', 'data-src', 'data-original', 'data-lazy-src')


def sniff_ext(data):
    """Detect the real image type from magic bytes (None if it isn't a known image)."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return '.png'
    if data.startswith(b'\xff\xd8\xff'): return '.jpg'
    if data[:6] in (b'GIF87a', b'GIF89a'): return '.gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return '.webp'
    if data[4:12] in (b'ftypavif', b'ftypavis'): return '.avif'
    if data.startswith(b'\x00\x00\x01\x00'): return '.ico'
    head = data[:2048].lstrip(b'\xef\xbb\xbf \r\n\t').lower()
    if head.startswith(b'<svg') or (head.startswith(b'<?xml') and b'<svg' in head): return '.svg'
    return None


def looks_like_html(data):
    head = data[:512].lstrip().lower()
    return head.startswith((b'<!doctype html', b'<html', b'<head', b'<body'))


def candidate_urls(url):
    """Where an image may actually live. The Academy front-end serves module images from the
    public CDN (`<CDN>/content/modules/<id>/...`), whichever way the markdown spells the path:
        /storage/modules/<id>/x.png  ->  <CDN>/content/modules/<id>/x.png
        /content/modules/<id>/x.png  ->  <CDN>/content/modules/<id>/x.png
    The Academy host itself answers 404 for those files, so try the CDN first and fall back to
    the URL exactly as written."""
    parts = urlsplit(url)
    if parts.netloc == urlsplit(HOST).netloc:
        path = None
        if parts.path.startswith('/storage/'):
            path = '/content/' + parts.path[len('/storage/'):]
        elif parts.path.startswith('/content/'):
            path = parts.path
        if path:
            mapped = CDN + path + ('?' + parts.query if parts.query else '')
            return [mapped, url]
    return [url]


def _fetch_one(url):
    r = session.get(url, timeout=60, headers={
        'User-Agent': HEADERS['User-Agent'],
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Referer': f'{HOST}/',
    })
    r.raise_for_status()
    data = r.content
    if not data:
        raise ValueError("empty response")
    ctype = r.headers.get('Content-Type', '').split(';')[0].strip().lower()
    ext = sniff_ext(data)
    if ext is None:
        if looks_like_html(data) or not ctype.startswith('image/'):
            raise ValueError(f"not an image (Content-Type: {ctype or 'unknown'}) - cookie expired?")
        ext = EXT_BY_MIME.get(ctype) or os.path.splitext(unquote(urlsplit(url).path))[1].lower() or '.img'
    return data, ext


def fetch_image(url):
    """Download one image, trying each candidate location. Returns (bytes, ext).
    Raises if none of them yields a real image (HTTP errors, or a 200 login page when the
    cookie has expired)."""
    errors = []
    for cand in candidate_urls(url):
        try:
            return _fetch_one(cand)
        except Exception as e:
            errors.append(f"{cand}: {e}")
    raise RuntimeError(" | ".join(errors))


def normalize_url(src):
    """Turn whatever the markdown contained into an absolute, fragment-free URL (or None to skip)."""
    src = (src or '').strip()
    if not src or src.startswith(('data:', 'blob:', 'about:', '#', 'javascript:')):
        return None
    return urldefrag(urljoin(HOST + '/', src))[0]


def src_of(tag):
    """Best image URL of an <img> (handles lazy-loading attributes and srcset-only tags)."""
    for attr in LAZY_ATTRS:
        if tag.get(attr, '').strip():
            return tag[attr]
    srcset = tag.get('srcset', '')
    cands = [c.strip().split()[0] for c in srcset.split(',') if c.strip()]
    return cands[-1] if cands else None


def is_image_link(href):
    return os.path.splitext(unquote(urlsplit(href or '').path))[1].lower() in IMG_EXTS


def pick_filename(url, ext, used):
    stem = os.path.splitext(unquote(os.path.basename(urlsplit(url).path)))[0]
    stem = re.sub(r'[^\w.\-]+', '_', stem).strip('._') or 'image'
    # Some diagram embeds (e.g. pako-encoded plantuml/kroki URLs) have a "filename" that's
    # really hundreds of chars of compressed data - Windows rejects any path component over
    # 255 chars with OSError 22. Truncate and disambiguate with a hash of the full URL.
    overlong = len(stem) > 120
    if overlong:
        stem = stem[:120]
    name = stem + ext
    if overlong or name.lower() in used:  # same basename, different URL -> disambiguate
        name = f"{stem}_{hashlib.sha1(url.encode()).hexdigest()[:8]}{ext}"
    used.add(name.lower())
    return name


def process_images(soups, assets_dir, inline=False, workers=8):
    """Download every image referenced by the parsed sections, then rewrite the tags.
    Images that cannot be fetched keep their absolute URL so they still work online."""
    img_dir = os.path.join(assets_dir, "images")
    if not inline:
        os.makedirs(img_dir, exist_ok=True)

    # 1. collect unique URLs (document order)
    urls, seen = [], set()
    for soup in soups:
        refs = [(t, src_of(t)) for t in soup.find_all("img")]
        refs += [(t, t.get("href")) for t in soup.find_all("a") if is_image_link(t.get("href"))]
        for _, raw in refs:
            u = normalize_url(raw)
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
    print(f"\x1b[1;32m\tDownloading {len(urls)} images\n\x1b[0m")

    # 2. download in parallel
    def job(u):
        try:
            return u, fetch_image(u), None
        except Exception as e:
            return u, None, e

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(job, urls))

    # 3. save (deterministic naming, in document order) / build data URIs
    ref, failed, used = {}, [], set()
    for u, res, err in results:
        if err is not None:
            failed.append((u, err))
            print(f"\t\x1b[1;31m✗ Failed: \x1b[0m{u} ({err})")
            continue
        data, ext = res
        if inline:
            mime = MIME_BY_EXT.get(ext, 'application/octet-stream')
            ref[u] = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        else:
            name = pick_filename(u, ext, used)
            with open(os.path.join(img_dir, name), 'wb') as f:
                f.write(data)
            ref[u] = f"images/{quote(name)}"  # pages live in assets/, images in assets/images/
            print(f"\t✓ \x1b[1;36m{name}\x1b[0m")

    # 4. rewrite the HTML
    for soup in soups:
        for img in soup.find_all("img"):
            u = normalize_url(src_of(img))
            if not u:
                continue
            for a in LAZY_ATTRS[1:] + ('srcset', 'sizes'):
                img.attrs.pop(a, None)
            img["src"] = ref.get(u, u)
            img["loading"] = "lazy"
            img.attrs.setdefault("alt", "")
            if u in ref and img.parent.name != "a" and not inline:
                link = soup.new_tag("a", href=img["src"], target="_blank")
                img.wrap(link)
        for a in soup.find_all("a"):
            if is_image_link(a.get("href")):
                u = normalize_url(a["href"])
                if u in ref and not inline:
                    a["href"] = ref[u]
    return failed


# === PATH MODE: download every module of a path ===
def safe_name(text):
    return re.sub(r'[<>:"/\\|?*]+', '_', str(text)).strip() or 'untitled'


# === PAGE NAVIGATION (Back / Forward buttons, same look as the existing HTB_Courses) ===
PAGE_NAV_STYLE = """<style>
.page-nav{position:fixed;left:50%;bottom:1.25rem;transform:translateX(-50%);display:flex;gap:.75rem;z-index:99999;font-family:'Inter',sans-serif;}
.page-nav a,.page-nav span{display:inline-flex;align-items:center;gap:.4rem;padding:.65rem 1.1rem;border-radius:2rem;font-weight:bold;font-size:.9rem;text-decoration:none;box-shadow:0 4px 10px rgba(0,0,0,0.35);transition:.2s;}
.page-nav a{background:#9fef00;color:#0f172a;}
.page-nav a:hover{background:#bfff40;transform:scale(1.05);}
.page-nav span.disabled{background:#334155;color:#64748b;cursor:not-allowed;box-shadow:none;}
</style>"""


def page_nav_block(back_href, fwd_href):
    back = f'<a href="{back_href}">← Back</a>' if back_href else '<span class="disabled">← Back</span>'
    fwd = f'<a href="{fwd_href}">Forward →</a>' if fwd_href else '<span class="disabled">Forward →</span>'
    return (f'\n<!-- PAGE-NAV:START -->\n{PAGE_NAV_STYLE}\n'
            f'<div class="page-nav">\n  {back}\n  {fwd}\n</div>\n<!-- PAGE-NAV:END -->\n')


def set_page_nav(filepath, back_href, fwd_href):
    """(Re)write the PAGE-NAV block of an already-saved page - used by run_path to stitch
    cross-module Back/Forward links once every module's real folder name and page count
    are known (a single module, generated in isolation, only knows about itself)."""
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()
    block = page_nav_block(back_href, fwd_href)
    if '<!-- PAGE-NAV:START -->' in html:
        html = re.sub(r'<!-- PAGE-NAV:START -->.*?<!-- PAGE-NAV:END -->', block.strip(), html, flags=re.S)
    else:
        html = html.replace('</body>', block + '</body>')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)


def module_page_numbers(module_dir):
    adir = os.path.join(module_dir, "assets")
    if not os.path.isdir(adir):
        return []
    return sorted(int(os.path.splitext(fn)[0]) for fn in os.listdir(adir) if re.fullmatch(r'\d+\.html', fn))


def run_path(path_id):
    mods = api_get_or_exit(f"{BASE_API}/paths/{path_id}/modules").get("data") or []
    if not mods:
        sys.exit(f"\x1b[1;31mPath {path_id} returned no modules (wrong ID, or not unlocked on your account).\x1b[0m")

    path_name = f"Path {path_id}"
    try:  # optional: nicer folder name; fall back silently if the endpoint differs
        r = session.get(f"{BASE_API}/paths/{path_id}", headers=HEADERS, timeout=30)
        d = r.json().get("data", {}) if r.ok else {}
        title = d.get("title") or d.get("name")
        if title:
            path_name = f"{path_id}. {title}"
    except Exception:
        pass
    root = os.path.join(args.out, safe_name(path_name))
    os.makedirs(root, exist_ok=True)
    print(f"\x1b[1;32mPath: \x1b[1;34m{path_name}\x1b[0m  ({len(mods)} modules)\n")

    env = dict(os.environ, HTB_COOKIE=cookie_str)  # keep the cookie out of `ps`
    done, failed, entries = [], [], []
    for i, m in enumerate(mods, 1):
        folder_prefix = f"{i:02d}. "
        existing = [d for d in os.listdir(root)
                    if d.startswith(folder_prefix) and os.path.exists(os.path.join(root, d, "index.html"))]
        print(f"\x1b[1;35m[{i}/{len(mods)}] {m['name']} (id {m['id']})\x1b[0m")
        if existing and args.resume:
            print("\t↷ already downloaded (--resume is on)\n")
            entries.append((i, m['name'], existing[0]))
            done.append(m['name'])
            continue
        cmd = [sys.executable, os.path.abspath(__file__), "--module", str(m['id']), "--order", str(i),
               "--out", root, "--workers", str(args.workers), "--host", HOST, "--cdn", CDN, "--quiet"]
        if args.inline:
            cmd.append("--inline")
        rc = subprocess.run(cmd, env=env).returncode
        folder = next((d for d in sorted(os.listdir(root))
                       if d.startswith(folder_prefix) and os.path.exists(os.path.join(root, d, "index.html"))), None)
        if rc == 0 and folder:
            done.append(m['name'])
            entries.append((i, m['name'], folder))
        else:
            failed.append(m['name'])
            print(f"\x1b[1;31m\t✗ {m['name']} failed (exit {rc})\x1b[0m")
        print()

    # Stitch cross-module Back/Forward links now that every module's real folder name and
    # page count are known - each module is generated in its own subprocess and only knows
    # about its own pages, so the two boundary links (its index's Back, its last page's
    # Forward) are left disabled until now and patched in place on disk.
    for idx, (_, _, folder) in enumerate(entries):
        pages = module_page_numbers(os.path.join(root, folder))
        if not pages:
            continue
        if idx == 0:
            back_href = "../index.html"
        else:
            prev_pages = module_page_numbers(os.path.join(root, entries[idx - 1][2]))
            back_href = (f"../{quote(entries[idx - 1][2])}/assets/{prev_pages[-1]}.html"
                         if prev_pages else "../index.html")
        set_page_nav(os.path.join(root, folder, "index.html"), back_href, f"assets/{pages[0]}.html")

        last_back = f"{pages[-2]}.html" if len(pages) > 1 else "../index.html"
        fwd_href = f"../../{quote(entries[idx + 1][2])}/index.html" if idx < len(entries) - 1 else None
        set_page_nav(os.path.join(root, folder, "assets", f"{pages[-1]}.html"), last_back, fwd_href)

    # path-level index linking all modules
    items = "\n".join(f'<li><a href="{quote(folder)}/index.html">{i:02d}. {htmllib.escape(name)}</a></li>'
                      for i, name, folder in entries)
    root_index = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{htmllib.escape(path_name)}</title>
<style>body{{background:#0f172a;color:#e2e8f0;font-family:sans-serif;max-width:800px;margin:3rem auto;line-height:2}}
a{{color:#b2f233;text-decoration:none}}a:hover{{text-decoration:underline}}h1{{color:#fff}}</style></head>
<body><h1>{htmllib.escape(path_name)}</h1><ol style="list-style:none;padding:0">{items}</ol></body></html>"""
    if entries:
        root_index = root_index.replace('</body></html>', page_nav_block(None, f"{quote(entries[0][2])}/index.html") + '</body></html>')
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as f:
        f.write(root_index)

    print(f"\x1b[1;32m🎉 {len(done)}/{len(mods)} modules ready\x1b[0m")
    if failed:
        print(f"\x1b[1;33mFailed: {', '.join(failed)} - re-run the same command (add --resume to skip finished ones).\x1b[0m")
    print(f"\tOpen \x1b[34m{root}/index.html\x1b[0m")
    sys.exit(1 if failed else 0)


if args.path:
    run_path(args.path)

# === GET MODULE INFO ===
module_json = api_get_or_exit(f"{BASE_API}/modules/{MODULE_ID}")
module = module_json["data"]
print(f"\x1b[1;32mModule: \x1b[1;34m{module['name']}\n\x1b[0m")
OUTPUT_DIR = os.path.join(args.out, (f"{args.order:02d}. " if args.order else f"{MODULE_ID}. ")
                          + re.sub(r'[<>:"/\\|?*]+', '_', module['name']).strip())
ASSETS_DIR = os.path.join(OUTPUT_DIR, "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

# === GET ALL SECTIONS ===
sections = module.get('sections', [])
print(f"\x1b[1;32m\tFound {len(sections)} sections\n\x1b[0m")

# === DOWNLOAD SECTION CONTENT ===
section_contents = {}
for sec in sections:
    sec_id = sec["id"]
    url = f"{BASE_API}/modules/{MODULE_ID}/sections/{sec_id}"
    try:
        data = api_get(url)["data"]
    except Exception as e:
        print(f"\x1b[1;31mFailed to fetch section {sec_id}: {e}\x1b[0m")
        continue
    md = data["content"]
    extensions = ['tables', 'fenced_code', 'codehilite', 'sane_lists']
    extension_configs = {
        'codehilite': {'linenums': False, 'guess_lang': False, 'css_class': 'codehilite'}
    }
    md = re.sub(r'\*\s*`([^`]+)`', r'\n* `\1`', md)
    html = markdown(md, extensions=extensions, extension_configs=extension_configs)
    soup = BeautifulSoup(html, 'html.parser')

    # === REMOVE MAIN H1 ===
    for h1 in soup.find_all('h1'):
        h1.decompose()

    # === STYLE INLINE CODE ===
    for code in soup.find_all('code'):
        if code.parent.name != 'pre':
            if code.string and any(term in code.string.lower()
                                   for term in ['shell', 'bash', 'tcsh', 'csh', 'ksh', 'zsh', 'fish', 'kernel']):
                code['style'] = 'color:#4589ff; background:none; padding:0; font-weight:bold;'
            else:
                code['style'] = 'background:rgb(30 41 57); color:#4589ff; padding:0.2rem 0.4rem; border-radius:0.3rem;'

    section_contents[sec_id] = {
        "title": data["title"],
        "soup": soup,
        "page": data.get("page", 0),
        "group": data.get("group", "Unknown")
    }

# === DOWNLOAD IMAGES + FIX <img> TAGS ===
failed_images = process_images([c["soup"] for c in section_contents.values()], ASSETS_DIR,
                               inline=args.inline, workers=args.workers)
for c in section_contents.values():
    c["html"] = str(c.pop("soup"))

# === SORT SECTIONS BY PAGE ===
sorted_sections = sorted(sections, key=lambda s: section_contents.get(s["id"], {}).get("page", 999))


# === MAP PAGE TO GROUP/TITLE ===
page_to_group, page_to_title = {}, {}
for sec in sorted_sections:
    sec_id = sec["id"]
    if sec_id not in section_contents:
        continue
    page = section_contents[sec_id]["page"]
    group = section_contents[sec_id]["group"]
    title = section_contents[sec_id]["title"]
    page_to_group[page] = group
    page_to_title[page] = title

# Group order by first appearance
group_order, seen_groups = [], set()
for page in sorted(page_to_group.keys()):
    g = page_to_group[page]
    if g not in seen_groups:
        seen_groups.add(g)
        group_order.append(g)

# === PYGMENTS CSS ===
formatter = HtmlFormatter(style='native')
pygments_css = formatter.get_style_defs('.codehilite')
with open(os.path.join(OUTPUT_DIR, "pygments.css"), "w") as f:
    f.write(pygments_css)

# === ICONS ===
article_icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="12" y1="18" x2="12" y2="12"></line><line x1="9" y1="15" x2="15" y2="15"></line></svg>'
dropdown_icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"></polyline></svg>'


# === STYLE.CSS (light/dark via CSS vars - toggled by #dark-toggle) ===
with open(os.path.join(OUTPUT_DIR, "style.css"), "w", encoding="utf-8") as f:
    f.write("""
    @import url('https://use.typekit.net/jxn7iyx.css');
    :root {
        --bg: #f8fafc;
        --sidebar: #ffffff;
        --card: #e2e8f0;
        --text: #0f172a;
        --green: #4d7c0f;
        --blue: #2563eb;
        --gray: #64748b;
        --hover: #cbd5e1;
        --border: #cbd5e1;
        --code-bg: #0f172a;
        --code-text: #e2e8f0;
    }
    html.dark {
        --bg: #0f172a;
        --sidebar: #1e293b;
        --card: #334155;
        --text: #e2e8f0;
        --green: #9fef00;
        --blue: #4589ff;
        --gray: #94a3b8;
        --hover: #475569;
        --border: #334155;
        --code-bg: #020617;
        --code-text: #e2e8f0;
    }
    body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; line-height: 1.6; transition: background 0.2s ease, color 0.2s ease; }
    h1 { color: var(--green) !important; font-size: 2.5rem; margin: 1rem 0; }
    h2, h3 { color: var(--blue); margin-top: 2rem; }
    .content { padding: 2rem; max-width: 900px; margin: 0 auto; }
    pre { background: var(--code-bg); color: var(--code-text); padding: 1rem; border-radius: 0.5rem; overflow-x: auto; margin-bottom: 1.5rem; }
    table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; }
    th, td { border: 1px solid var(--border); padding: 0.75rem; text-align: left; }
    th { background: var(--card); }
    img { max-width: 100%; height: auto; border-radius: 0.5rem; margin: 1rem 0; }
    .back-link { color: var(--green); font-weight: bold; text-decoration: none; }
    .back-link:hover { text-decoration: underline; }
    hr { border: 1px solid var(--border); margin: 2rem 0; }
    a { color: var(--green); text-decoration: none; transition: color 0.2s ease; }
    a:hover { color: var(--blue); text-decoration: underline; }
    h1, h2, h3, h4, h5, h6 { color: var(--text); }
    p { color: var(--text); }
    pre code { background: none; color: inherit; padding: 0; box-shadow: none; display: block; }

    /* TOC PANEL */
    .toc-panel {
        position: fixed; top: 0; right: 0; width: 380px; height: 100%; background: var(--sidebar);
        box-shadow: -10px 0 30px rgba(0,0,0,0.5); z-index: 9999; overflow-y: auto; transition: transform 0.3s ease;
        transform: translateX(100%);
    }
    .toc-panel.open { transform: translateX(0); }
    .toc-header {
        padding: 1.5rem; background: #242f40; display: flex; justify-content: space-between; align-items: center;
        color: white; font-weight: bold; font-size: 1.2rem; position: sticky; top: 0; z-index: 10;
    }
    .toc-toggle {
        position: fixed; top: 1rem; right: 1rem; z-index: 99999; background: var(--green); color: black;
        border: none; padding: 0.75rem 1rem; border-radius: 2rem; cursor: pointer; font-weight: bold;
        box-shadow: 0 4px 10px rgba(159, 239, 0, 0.3); transition: 0.2s;
    }
    .toc-toggle:hover { background: #bfff40; transform: scale(1.05); }
    .toc-close { background: none; border: none; color: var(--gray); font-size: 1.8rem; cursor: pointer; }
    .toc-close:hover { color: var(--text); }
    .toc-body { padding: 1rem; }
    .toc-group { margin-bottom: 1rem; }
    .toc-group-header {
        display: flex; align-items: center; padding: 1rem;
        background: var(--card); border-radius: 0.75rem; cursor: pointer;
        transition: 0.2s; font-weight: 600;
    }
    .toc-group-header:hover { background: var(--hover); }
    .toc-num {
        background: var(--blue); color: white; width: 32px; height: 32px;
        border-radius: 50%; display: flex; align-items: center; justify-content: center;
        font-size: 1rem; margin-right: 1rem; font-weight: bold;
    }
    .toc-title { flex-grow: 1; }
    .toc-count { color: var(--gray); font-size: 0.9rem; margin-right: 0.5rem; }
    .toc-arrow { transition: transform 0.2s; color: var(--gray); }
    .toc-group input { display: none; }
    .toc-group input:checked ~ .toc-group-header .toc-arrow { transform: rotate(180deg); }
    .toc-group input:checked ~ .toc-sections { max-height: 1000px; opacity: 1; }
    .toc-sections {
        max-height: 0; opacity: 0; overflow: hidden; transition: all 0.3s ease;
        margin-top: 0.5rem;
    }
    .toc-section {
        padding: 0.75rem 1rem; display: flex; align-items: center;
        border-bottom: 1px solid var(--border); transition: 0.2s;
    }
    .toc-section:hover { background: var(--hover); }
    .toc-section a {
        color: var(--text); text-decoration: none; flex-grow: 1; font-size: 0.95rem;
        display: flex; align-items: center;
    }
    .toc-section a:hover { color: var(--green); }
    .toc-icon {
        margin-right: 0.75rem; opacity: 0.7; width: 16px; height: 16px;
    }
    .toc-article { color: var(--gray); font-size: 0.8rem; margin-left: auto; }
    #dark-toggle {
        position: fixed; top: 1rem; left: 1rem; z-index: 99999;
        background: var(--card); color: var(--text); border: none;
        padding: 0.75rem; border-radius: 50%; cursor: pointer; font-size: 1.2rem;
    }
    """)

# === TOC BODY ===
toc_body = ""
group_num = 1
for group_name in group_order:
    try:
        safe_group = re.sub(r'[^a-zA-Z0-9_-]', '_', str(group_name))
    except:
        safe_group = group_name
    group_pages = [p for p, g in page_to_group.items() if g == group_name]
    toc_body += f'''
    <div class="toc-group">
        <input type="checkbox" id="group_{safe_group}" checked>
        <label for="group_{safe_group}" class="toc-group-header">
            <div class="toc-num">{group_num}</div>
            <div class="toc-title">{group_name}</div>
            <div class="toc-count">{len(group_pages)} Sections</div>
            <div class="toc-arrow">{dropdown_icon}</div>
        </label>
        <div class="toc-sections">'''
    for page in sorted(group_pages):
        title = page_to_title[page]
        toc_body += f'''
            <div class="toc-section">
                <div class="toc-icon">{article_icon}</div>
                <a href="#" data-file="{page}.html" onclick="navigate(event)">{htmllib.escape(title)}</a>
                <span class="toc-article">Article</span>
            </div>'''
    toc_body += '''
        </div>
    </div>'''
    group_num += 1

toc_html = f'''
<button class="toc-toggle" onclick="document.getElementById('toc-panel').classList.toggle('open')">☰</button>
<div id="toc-panel" class="toc-panel">
  <div class="toc-header">
    <span>Table of Contents</span>
    <button class="toc-close" onclick="document.getElementById('toc-panel').classList.remove('open')">×</button>
  </div>
  <div class="toc-body">
    {toc_body}
  </div>
</div>
<script>
function navigate(e) {{
  e.preventDefault();
  var file = e.currentTarget.getAttribute('data-file');
  var prefix = location.pathname.includes('/assets/') ? '' : 'assets/';
  location.href = prefix + file;
}}
</script>
'''

# === PAGES ===
page_htmls = {}
for sec in sorted_sections:
    sec_id = sec["id"]
    if sec_id not in section_contents:
        continue
    title = section_contents[sec_id]["title"]
    page = section_contents[sec_id]["page"]
    filename = f"{page}.html"
    page_htmls[filename] = f"""
    <!DOCTYPE html><html class="dark"><head><title>{htmllib.escape(title)}</title>
    <meta charset="utf-8">
    <link href="../style.css" rel="stylesheet">
    <link href="../pygments.css" rel="stylesheet">
    <link rel="icon" type="image/x-icon" href="https://academy.hackthebox.com/favicon.ico">
    </head><body>
    {toc_html}
    <div class="content">
    <a href="#" class="back-link" onclick="document.getElementById('toc-panel').classList.toggle('open'); return false;">← Back to TOC</a>
    <h1>{htmllib.escape(title)}</h1>
    {section_contents[sec_id]["html"]}
    <hr>
    <a href="#" class="back-link" onclick="document.getElementById('toc-panel').classList.toggle('open'); return false;">Back to TOC</a>
    </div>
    <button id="dark-toggle" onclick="document.documentElement.classList.toggle('dark')">🌓</button>
    </body></html>
    """

# === PAGE NAV (Back/Forward through this module's own pages) ===
# The module index's "Back" and the last page's "Forward" point outside this module
# (previous/next module in the path); when run standalone (no --path) there's nothing to
# point them at, so they stay disabled. run_path patches both in afterwards once every
# module has been generated and their real folder names/page counts are known.
page_seq = list(page_htmls.keys())
for idx, fname in enumerate(page_seq):
    back_href = page_seq[idx - 1] if idx > 0 else "../index.html"
    fwd_href = page_seq[idx + 1] if idx < len(page_seq) - 1 else None
    page_htmls[fname] = page_htmls[fname].replace(
        '</body></html>', page_nav_block(back_href, fwd_href) + '</body></html>')

# === TOC PAGE (CLEANED) ===
toc_page = f"""<!DOCTYPE html><html class="dark"><head><title>{module['name']} Offline</title>
    <meta charset="utf-8">
    <link href="style.css" rel="stylesheet">
    <link href="pygments.css" rel="stylesheet">
    </head><body>
    {toc_html}
    <div class="content">
    <h1>{module['name']} Offline</h1>
    <p><strong>Click ☰</strong> → HTB Sidebar!</p>
    <hr>
    </div>
    <script>document.getElementById('toc-panel').classList.add('open');</script>
    </body></html>"""
if page_seq:
    toc_page = toc_page.replace('</body></html>', page_nav_block(None, f"assets/{page_seq[0]}") + '</body></html>')
with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
    f.write(toc_page)

# === SAVE PAGES ===
for filename, content in page_htmls.items():
    with open(os.path.join(ASSETS_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)

print(f"\t\x1b[1;32m🎉 FINAL PERFECTION!\n")
if failed_images:
    print(f"\t\x1b[1;33m{len(failed_images)} image(s) could not be downloaded and still point at the online URL.\x1b[0m")
print(f"\tOpen \x1b[34m{OUTPUT_DIR}/index.html\x1b[0m")
