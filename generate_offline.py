import os
import re
import argparse
import requests
from bs4 import BeautifulSoup
from markdown import markdown
from pygments.formatters import HtmlFormatter
from urllib.parse import urljoin

# Install required: pip install requests beautifulsoup4 markdown pygments
# === BANNER ===

print("""\x1b[1;34m
██╗  ██╗████████╗██████╗     ██████╗  █████╗ ██████╗ ███████╗███████╗██████╗ 
██║  ██║╚══██╔══╝██╔══██╗    ██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔════╝██╔══██╗
███████║   ██║   ██████╔╝    ██████╔╝███████║██████╔╝███████╗█████╗  ██████╔╝
██╔══██║   ██║   ██╔══██╗    ██╔═══╝ ██╔══██║██╔══██╗╚════██║██╔══╝  ██╔══██╗
██║  ██║   ██║   ██████╔╝    ██║     ██║  ██║██║  ██║███████║███████╗██║  ██║
╚═╝  ╚═╝   ╚═╝   ╚═════╝     ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝
                                                                             
                        \x1b[1;35m@viperzcrew2 on Telegram\x1b[0m
""")
# === ARGPARSE ===
parser = argparse.ArgumentParser(description='Generate offline HTB module')
parser.add_argument('--module', required=True, type=int, help='Module ID (required)')
parser.add_argument('--cookie', required=False, help='Cookie string (optional; if not provided, read from cookie.txt)')
args = parser.parse_args()

# === CONFIG ===
MODULE_ID = args.module
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
    'Accept': 'application/json',
    'Referer': f'https://academy.hackthebox.com/beta/module/{MODULE_ID}',
}
BASE_API = "https://academy.hackthebox.com/api/v2"

if args.cookie:
    HEADERS['Cookie'] = args.cookie
else:
    if os.path.exists("cookie.txt"):
        with open("cookie.txt", "r") as f:
            cookie = f.read().strip()
            HEADERS['Cookie'] = cookie
    else:
        raise ValueError("No --cookie provided and cookie.txt not found.")

# === DOWNLOAD ASSET ===
def download(url):
    if not url.startswith("http"):
        url = "https://academy.hackthebox.com" + url
    filename = os.path.basename(url.split("?")[0])
    path = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(path):
        try:
            r = requests.get(url, headers=HEADERS)
            if r.status_code == 200:
                with open(path, 'wb') as f:
                    f.write(r.content)
                print(f"\t✓ \x1b[1;36m{filename}\x1b[0m")
        except Exception as e:
            print(f"\t\x1b[1;31m✗ Failed: \x1b[0m{url} ({e})")
    # return just filename (important fix)
    return filename

# === GET MODULE INFO ===
module_json = requests.get(f"{BASE_API}/modules/{MODULE_ID}", headers=HEADERS).json()
module = module_json["data"]
print(f"\x1b[1;32mModule: \x1b[1;34m{module['name']}\n\x1b[0m")
OUTPUT_DIR = f"{MODULE_ID}. {module['name']}"
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
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    if resp.status_code != 200:
        print(f"\x1b[1;31mFailed to fetch section {sec_id}: {resp.status_code}\x1b[0m")
        continue
    data = resp.json()["data"]
    md = data["content"]
    extensions = ['tables', 'fenced_code', 'codehilite', 'sane_lists']
    extension_configs = {
        'codehilite': {'linenums': False, 'guess_lang': False, 'css_class': 'codehilite'}
    }
    md = re.sub(r'\*\s*`([^`]+)`', r'\n* `\1`', md)
    html = markdown(md, extensions=extensions, extension_configs=extension_configs)
    soup = BeautifulSoup(html, 'html.parser')

    # === FIX IMAGES ===
    for img in soup.find_all("img"):
        img["src"] = download(img["src"])

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
        "html": str(soup),
        "page": data.get("page", 0),
        "group": data.get("group", "Unknown")
    }

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


# === STYLE.CSS (HTB GREEN TITLE + FIXED BUTTON) ===
with open(os.path.join(OUTPUT_DIR, "style.css"), "w", encoding="utf-8") as f:
    f.write("""
    @import url('https://use.typekit.net/jxn7iyx.css');
    :root { 
        --bg: #0f172a; 
        --sidebar: #1e293b;
        --card: #334155;
        --text: #e2e8f0;
        --green: #9fef00;
        --blue: #4589ff;
        --gray: #94a3b8;
        --hover: #475569;
    }
    body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; line-height: 1.6; }
    h1 { color: var(--green) !important; font-size: 2.5rem; margin: 1rem 0; }
    h2, h3 { color: var(--blue); margin-top: 2rem; }
    .content { padding: 2rem; max-width: 900px; margin: 0 auto; }
    pre { background: #020617; padding: 1rem; border-radius: 0.5rem; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; }
    th, td { border: 1px solid #475569; padding: 0.75rem; text-align: left; }
    th { background: var(--card); }
    img { max-width: 100%; border-radius: 0.5rem; margin: 1rem 0; }
    .back-link { color: var(--green); font-weight: bold; text-decoration: none; }
    .back-link:hover { text-decoration: underline; }
    hr { border: 1px solid #334155; margin: 2rem 0; }

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
    .toc-close:hover { color: white; }
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
        border-bottom: 1px solid #334155; transition: 0.2s;
    }
    .toc-section:hover { background: #2d3748; }
    .toc-section a {
        color: var(--text); text-decoration: none; flex-grow: 1; font-size: 0.95rem;
        display: flex; align-items: center;
    }
    .toc-section a:hover { color: white; }
    .toc-icon {
        margin-right: 0.75rem; opacity: 0.7; width: 16px; height: 16px;
    }
    .toc-article { color: var(--gray); font-size: 0.8rem; margin-left: auto; }
    #dark-toggle {
        position: fixed; top: 1rem; left: 1rem; z-index: 99999;
        background: var(--card); color: white; border: none;
        padding: 0.75rem; border-radius: 50%; cursor: pointer; font-size: 1.2rem;
    }
         a {
  color: rgb(178 242 51 / 1); /* exakt #B2F233, das HTB-Grün */
  text-decoration: none;
  transition: color 0.2s ease;
}

a:hover {
  color: rgb(190 255 100 / 1);
  text-decoration: underline;
}

/* falls du Back-Link separat stylen willst */
.back-link {
  color: rgb(178 242 51 / 1);
  font-weight: bold;
}   h1, h2, h3, h4, h5, h6 {
  color: #ffffff !important;
}

p {
  color: var(--text);
}

pre {
  background-color: rgb(30 41 57);
  color: #e2e8f0;
  padding: 1rem;
  border-radius: 0.5rem;
  overflow-x: auto;
  margin-bottom: 1.5rem;
}

pre code { 
  background: none;
  color: inherit;
  padding: 0;
  box-shadow: none;
  display: block;
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
                <a href="#" data-file="{page}.html" onclick="navigate(event)">{title}</a>
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
    <!DOCTYPE html><html class="dark"><head><title>{title}</title>
    <meta charset="utf-8">
    <link href="../style.css" rel="stylesheet">
    <link href="../pygments.css" rel="stylesheet">
    <link rel="icon" type="image/x-icon" href="https://academy.hackthebox.com/favicon.ico">
    </head><body>
    {toc_html}
    <div class="content">
    <a href="#" class="back-link" onclick="document.getElementById('toc-panel').classList.toggle('open'); return false;">← Back to TOC</a>
    <h1>{title}</h1>
    {section_contents[sec_id]["html"]}
    <hr>
    <a href="#" class="back-link" onclick="document.getElementById('toc-panel').classList.toggle('open'); return false;">Back to TOC</a>
    </div>
    <button id="dark-toggle" onclick="document.documentElement.classList.toggle('dark')">🌓</button>
    </body></html>
    """

# === TOC PAGE (CLEANED) ===
with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
    f.write(f"""<!DOCTYPE html><html class="dark"><head><title>{module['name']} Offline</title>
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
    </body></html>""")

# === SAVE PAGES ===
for filename, content in page_htmls.items():
    with open(os.path.join(ASSETS_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)

print(f"\t\x1b[1;32m🎉 FINAL PERFECTION!\n")
print(f"\tOpen \x1b[34m{OUTPUT_DIR}/index.html\x1b[0m")