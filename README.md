# Cloudflare DNS CLI

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

A lightweight **Cloudflare DNS CLI** written in **pure Python stdlib** (no external dependencies).
Manage your DNS records directly from the terminal on macOS/Linux.

---

## Features

- **Zones** -- list all zones accessible with your API token
- **Records** -- list, filter, add, update, delete DNS records
- **Safe** -- confirmation prompts for destructive actions (`--yes` to skip)
- **Simple output** -- human-friendly one-liners for add/update/delete
- **No pip needed** -- uses only Python 3 standard library (`urllib.request`)

---

## Installation

Clone or copy the script:

```bash
git clone https://github.com/hreskiv/cloudflare-dns-cli
cd cloudflare-dns-cli
chmod +x cf-dns.py
```
```bash
curl -L -O https://github.com/hreskiv/cloudflare-dns-cli/raw/refs/heads/main/cf-dns.py
chmod +x cf-dns.py
```

## Authentication

Generate a Scoped API Token in the [Cloudflare Dashboard](https://dash.cloudflare.com/?to=/:account/api-tokens):

**Permissions:**
- Zone > Zone > Read
- Zone > DNS > Read
- Zone > DNS > Edit

**Resources:**
- All zones (or select specific zones)

Export it in your shell (~/.zshrc or ~/.bashrc):
```bash
export CF_API_TOKEN="your_long_token_here"
```

Reload shell:
```bash
source ~/.zshrc
```

Or keep it in a file and pass with --token-file.

## Usage
### Run without arguments to see help and examples:
```bash
cf-dns.py
```

### Zones
```bash
cf-dns.py zones | column -t
```

### List records
```bash
cf-dns.py list example.com | column -t
cf-dns.py list example.com --type TXT
cf-dns.py list example.com --name-substr _acme
```
### Add record
```bash
cf-dns.py add example.com --name www --type A --content 203.0.113.10 --ttl 300 --proxied on
Output:
Record www.example.com (A) created: 203.0.113.10 (ttl=300 proxied=True)
```
### Update record
```bash
cf-dns.py update example.com --name www --type A --content 203.0.113.20
Output:
Record www.example.com (A) updated: 203.0.113.10 -> 203.0.113.20
```
### Delete record
```bash
cf-dns.py delete example.com --name www --type A
Output:
Record www.example.com (A) deleted: 203.0.113.20
```

## Notes

- Requires Python 3.8+ (tested on 3.13).
- Designed for API Tokens (not Global API Keys).
- For safety, add/update/delete ask for confirmation unless `--yes` is used.

## License

MIT -- feel free to fork, improve, and use in your projects.
