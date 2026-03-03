#!/usr/bin/env python3
"""
Cloudflare DNS CLI (stdlib only)
- zones / list / add / update / delete DNS records
- token from env CF_API_TOKEN (default) or --token-file
- add/update/delete ask for confirmation (skip with --yes)
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

__version__ = "1.2.0"

API_BASE = "https://api.cloudflare.com/client/v4"

def die(msg, code=1, data=None):
    print(msg, file=sys.stderr)
    if data is not None:
        try: print(json.dumps(data, indent=2), file=sys.stderr)
        except Exception: pass
    sys.exit(code)

def read_token(token_file):
    t = os.environ.get("CF_API_TOKEN")
    if t: return t.strip()
    if token_file:
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                return f.readline().strip()
        except Exception as e:
            die(f"Error reading token file: {e}", 2)
    die("No token. Set CF_API_TOKEN or use --token-file /path/to/token.txt", 2)

def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"cf-dns-cli-stdlib/{__version__}",
    }

def http_json(method, url, token, payload=None, params=None):
    if params:
        q = urllib.parse.urlencode(params)
        url = f"{url}?{q}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers(token))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            js = json.loads(body)
            if not js.get("success", False):
                die(f"Cloudflare API error (HTTP {resp.status})", data=js)
            return js
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try: js = json.loads(body)
        except Exception: die(f"HTTP {e.code}: {body[:4000]}")
        die(f"Cloudflare API error (HTTP {e.code})", data=js)
    except urllib.error.URLError as e:
        die(f"Network error: {e}")

def zone_id(token, zone_name, base=API_BASE):
    js = http_json("GET", f"{base}/zones", token, params={"name": zone_name})
    res = js.get("result") or []
    if not res: die(f"Zone not found or unauthorized: {zone_name}", 3)
    return res[0]["id"]

def paginate_records(token, zid, rtype=None, base=API_BASE):
    page, per = 1, 100
    out = []
    while True:
        params = {"page": page, "per_page": per}
        if rtype: params["type"] = rtype
        js = http_json("GET", f"{base}/zones/{zid}/dns_records", token, params=params)
        out.extend(js.get("result") or [])
        info = js.get("result_info") or {}
        if page >= int(info.get("total_pages", 1)): break
        page += 1
    return out

def render_table(records):
    print("id\ttype\tname\tcontent\tttl\tproxied")
    for r in records:
        print(f"{r.get('id','')}\t{r.get('type','')}\t{r.get('name','')}\t{r.get('content','')}\t{r.get('ttl','')}\t{r.get('proxied','')}")

def normalize_name(zone, name):
    return name if (name == zone or name.endswith("." + zone)) else f"{name}.{zone}"

def build_payload(name, rtype, content, ttl=None, proxied=None):
    p = {"type": rtype, "name": name, "content": content}
    if ttl is not None: p["ttl"] = int(ttl)
    if rtype in ("A","AAAA","CNAME") and proxied is not None: p["proxied"] = bool(proxied)
    return p

def find_record(token, zid, rid, name, rtype, zone_name, base=API_BASE):
    if rid:
        js = http_json("GET", f"{base}/zones/{zid}/dns_records/{rid}", token)
        return rid, js["result"]
    if not name or not rtype:
        die("Provide --id OR (--name and --type)", 2)
    fqdn = normalize_name(zone_name, name) if zone_name else name
    js = http_json("GET", f"{base}/zones/{zid}/dns_records", token,
                   params={"type": rtype, "name": fqdn})
    matches = js.get("result") or []
    if not matches: die(f"No records match: type={rtype} name={fqdn}", 3)
    if len(matches) == 1:
        r = matches[0]; return r["id"], r
    if not sys.stdin.isatty():
        die(f"Multiple records match type={rtype} name={fqdn}. Use --id to specify.", 3)
    print("Multiple records found:\n")
    render_table(matches)
    choice = input("\nEnter record ID to proceed (blank to cancel): ").strip()
    if not choice: print("No record selected.", file=sys.stderr); sys.exit(0)
    sel = next((m for m in matches if m.get("id")==choice), None)
    if not sel: die("Invalid record ID.", 2)
    return choice, sel

def cmd_zones(token, base):
    print("id\tname\tstatus\tplan")
    page, per = 1, 50
    while True:
        js = http_json("GET", f"{base}/zones", token, params={"page": page, "per_page": per})
        for z in js.get("result", []):
            print(f"{z.get('id','')}\t{z.get('name','')}\t{z.get('status','')}\t{(z.get('plan') or {}).get('name','')}")
        info = js.get("result_info") or {}
        if page >= int(info.get("total_pages", 1)): break
        page += 1

def cmd_list(token, zone, rtype, name_substr, as_json, base):
    zid = zone_id(token, zone, base)
    recs = paginate_records(token, zid, rtype, base)
    if name_substr:
        needle = name_substr.lower()
        recs = [r for r in recs if needle in str(r.get("name","")).lower()]
    if as_json: print(json.dumps(recs, indent=2))
    else: render_table(recs)

def confirm(prompt, yes):
    if yes: return
    ans = input(f"{prompt} [y/N] ").strip().lower()
    if ans not in ("y","yes"):
        print("Aborted.", file=sys.stderr)
        sys.exit(0)

def cmd_add(token, zone, name, rtype, content, ttl, proxied, yes, base):
    zid = zone_id(token, zone, base)
    fqdn = normalize_name(zone, name)
    rtype = rtype.upper()
    payload = build_payload(fqdn, rtype, content, ttl, proxied)
    print(f"About to CREATE: {rtype} {fqdn} → {content}")
    confirm("Proceed?", yes)
    js = http_json("POST", f"{base}/zones/{zid}/dns_records", token, payload=payload)
    r = js["result"]
    print(f"Record {r.get('name')} ({r.get('type')}) created: {r.get('content')} (ttl={r.get('ttl')} proxied={r.get('proxied')})")

def cmd_update(token, zone, rid, name, rtype, content, ttl, proxied, yes, base):
    zid = zone_id(token, zone, base)
    rtype = rtype.upper() if rtype else None
    rid, current = find_record(token, zid, rid, name, rtype, zone, base)
    new_name = normalize_name(zone, name) if name else current.get("name")
    new_type = rtype or current.get("type")
    new_content = content if content is not None else current.get("content")
    new_ttl = ttl if ttl is not None else current.get("ttl")
    prox_eff = proxied if proxied is not None else (bool(current.get("proxied")) if isinstance(current.get("proxied"), bool) else None)
    payload = build_payload(new_name, new_type, new_content, new_ttl, prox_eff)
    print("About to UPDATE:")
    print(f"from: {current.get('type')} {current.get('name')} → {current.get('content')}")
    print(f"to:   {new_type} {new_name} → {new_content}")
    confirm("Proceed?", yes)
    js = http_json("PUT", f"{base}/zones/{zid}/dns_records/{rid}", token, payload=payload)
    r = js["result"]
    print(f"Record {r.get('name')} ({r.get('type')}) updated: {current.get('content')} → {r.get('content')}")

def cmd_delete(token, zone, rid, name, rtype, yes, base):
    zid = zone_id(token, zone, base)
    rtype = rtype.upper() if rtype else None
    rid, current = find_record(token, zid, rid, name, rtype, zone, base)
    print(f"About to DELETE: {current.get('type')} {current.get('name')} → {current.get('content')}")
    confirm("Proceed?", yes)
    http_json("DELETE", f"{base}/zones/{zid}/dns_records/{rid}", token)
    print(f"Record {current.get('name')} ({current.get('type')}) deleted: {current.get('content')}")

def main():
    ap = argparse.ArgumentParser(description="Cloudflare DNS CLI (stdlib only)", formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--token-file", help="Read API token from file (first line). If omitted, uses CF_API_TOKEN.")
    ap.add_argument("--base-url", default=API_BASE, help="Override API base URL.")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompts for add/update/delete.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("zones", help="List all zones")

    pl = sub.add_parser("list", help="List DNS records in a zone")
    pl.add_argument("zone"); pl.add_argument("--type", dest="rtype"); pl.add_argument("--name-substr")
    pl.add_argument("--json", action="store_true")

    pa = sub.add_parser("add", help="Create DNS record")
    pa.add_argument("zone"); pa.add_argument("--name", required=True)
    pa.add_argument("--type", dest="rtype", required=True)
    pa.add_argument("--content", required=True)
    pa.add_argument("--ttl", type=int)
    pa.add_argument("--proxied", choices=["on","off"])

    pu = sub.add_parser("update", help="Update DNS record")
    pu.add_argument("zone"); pu.add_argument("--id", dest="rid")
    pu.add_argument("--name"); pu.add_argument("--type", dest="rtype")
    pu.add_argument("--content"); pu.add_argument("--ttl", type=int)
    pu.add_argument("--proxied", choices=["on","off"])

    pd = sub.add_parser("delete", help="Delete DNS record")
    pd.add_argument("zone"); pd.add_argument("--id", dest="rid")
    pd.add_argument("--name"); pd.add_argument("--type", dest="rtype")

    if len(sys.argv) == 1:
        print("""Cloudflare DNS CLI (stdlib only)

Usage:
  cf-dns.py zones
  cf-dns.py list <zone> [--type TYPE] [--name-substr STR] [--json]
  cf-dns.py add <zone> --name NAME --type TYPE --content VALUE [--ttl N] [--proxied on|off]
  cf-dns.py update <zone> (--id ID | --name NAME --type TYPE) [--content VALUE] [--ttl N] [--proxied on|off]
  cf-dns.py delete <zone> (--id ID | --name NAME --type TYPE)

Examples:
  cf-dns.py zones | column -t
  cf-dns.py list example.com | column -t
  cf-dns.py add example.com --name www --type A --content 203.0.113.10 --ttl 300 --proxied on
  cf-dns.py update example.com --name www --type A --content 203.0.113.20
  cf-dns.py delete example.com --name www --type A
""")
        sys.exit(0)

    args = ap.parse_args()
    token = read_token(args.token_file)
    base = args.base_url

    if args.cmd == "zones":
        cmd_zones(token, base)
    elif args.cmd == "list":
        rtype = args.rtype.upper() if args.rtype else None
        cmd_list(token, args.zone, rtype, args.name_substr, args.json, base)
    elif args.cmd == "add":
        prox = None
        if args.proxied is not None: prox = (args.proxied.lower()=="on")
        cmd_add(token, args.zone, args.name, args.rtype, args.content, args.ttl, prox, args.yes, base)
    elif args.cmd == "update":
        prox = None
        if args.proxied is not None: prox = (args.proxied.lower()=="on")
        cmd_update(token, args.zone, args.rid, args.name, args.rtype, args.content, args.ttl, prox, args.yes, base)
    elif args.cmd == "delete":
        cmd_delete(token, args.zone, args.rid, args.name, args.rtype, args.yes, base)

if __name__ == "__main__":
    main()
