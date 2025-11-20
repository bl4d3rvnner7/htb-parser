# 🖥️ HTB Module Parser

**Convert any unlocked HackTheBox Academy module into a complete offline HTML package** — including images, assets, code highlighting, TOC sidebar, and dark mode. ⚙️📘

## 📦 Project Structure

```
/
├── generate_offline.py
├── resources/
│   ├── Tutorial Vid.mp4
│   └── cookie_tut.png
└── README.md
```

## 🎬 Resources (Tutorial & Cookie Guide)

* **Tutorial Vid.mp4** — A full video walkthrough explaining how to use the script.
* **cookie_tut.png** — Screenshot showing how to extract your HTB session cookie from the browser.

You can find both files in the `resources` folder.

## ⚙️ Usage

### 1️⃣ Install dependencies

```bash
pip install requests beautifulsoup4 markdown pygments
```

### 2️⃣ Get your HTB cookie

1. Log into academy.hackthebox.com
2. Open *Developer Tools → Network*
3. Click any module request
4. Copy the **Cookie** header (see `resources/cookie_tut.png`)
5. Save it to `cookie.txt`

### 3️⃣ Run the module parser

```bash
python generate_offline.py --module <MODULE_ID>
```

Example:

```bash
python generate_offline.py --module 278
```

Optional: supply cookie inline

```bash
python generate_offline.py --module 278 --cookie "auth_sid=..."
```

## 📁 Output

The script generates a folder:

```
<MODULE_ID>. <Module Name>/
   ├── index.html
   ├── style.css
   ├── pygments.css
   └── assets/
        ├── 0.html
        ├── 1.html
        ├── image.png
        └── ...
```

Complete offline access with navigation & styling.

## 🤝 Contribute

Pull requests are welcome. Feel free to improve parsing, layout, or compatibility with future HTB versions.

