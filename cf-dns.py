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

__version__ = "1.3.0"

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
        ttl = r.get('ttl','')
        if ttl == 1: ttl = 'auto'
        print(f"{r.get('id','')}\t{r.get('type','')}\t{r.get('name','')}\t{_summarize(r)}\t{ttl}\t{r.get('proxied','')}")

def normalize_name(zone, name):
    if name in ("@", ""): return zone
    return name if (name == zone or name.endswith("." + zone)) else f"{name}.{zone}"

def build_payload(name, rtype, content, ttl=None, proxied=None, *,
                  priority=None, weight=None, port=None, target=None,
                  caa_flags=None, caa_tag=None, caa_value=None):
    p = {"type": rtype, "name": name}
    if rtype == "MX":
        if content is None: die("MX requires --content (mail server hostname)", 2)
        if priority is None: die("MX requires --priority N (0-65535)", 2)
        p["content"] = content
        p["priority"] = int(priority)
    elif rtype == "SRV":
        missing = [k for k,v in (("--priority",priority),("--weight",weight),("--port",port),("--target",target)) if v is None]
        if missing: die(f"SRV requires {' '.join(missing)}", 2)
        parts = name.split(".", 2)
        if len(parts) < 3 or not parts[0].startswith("_") or not parts[1].startswith("_"):
            die("SRV name must be _service._proto.host (e.g. _sip._tcp.example.com)", 2)
        service, proto, srv_name = parts[0], parts[1], parts[2]
        p["data"] = {
            "service": service, "proto": proto, "name": srv_name,
            "priority": int(priority), "weight": int(weight),
            "port": int(port), "target": target,
        }
    elif rtype == "CAA":
        if caa_tag is None or caa_value is None:
            die("CAA requires --caa-tag (issue|issuewild|iodef) and --caa-value", 2)
        if caa_tag not in ("issue","issuewild","iodef"):
            die("CAA --caa-tag must be one of: issue, issuewild, iodef", 2)
        p["data"] = {
            "flags": int(caa_flags) if caa_flags is not None else 0,
            "tag": caa_tag,
            "value": caa_value,
        }
    else:
        if content is None: die(f"{rtype} requires --content", 2)
        p["content"] = content
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

def _summarize(r):
    t = r.get('type')
    c = r.get('content','')
    if t == 'MX' and r.get('priority') is not None:
        c = f"{r.get('priority')} {c}"
    elif t in ('SRV','CAA'):
        d = r.get('data') or {}
        if t == 'SRV':
            c = f"{d.get('priority','?')} {d.get('weight','?')} {d.get('port','?')} {d.get('target','?')}"
        else:
            c = f"{d.get('flags',0)} {d.get('tag','?')} {d.get('value','?')}"
    return c

def cmd_add(token, zone, name, rtype, content, ttl, proxied, yes, base, **extra):
    zid = zone_id(token, zone, base)
    fqdn = normalize_name(zone, name)
    rtype = rtype.upper()
    payload = build_payload(fqdn, rtype, content, ttl, proxied, **extra)
    preview = {"type": rtype, "content": payload.get("content"),
               "priority": payload.get("priority"), "data": payload.get("data")}
    print(f"About to CREATE: {rtype} {fqdn} → {_summarize(preview)}")
    confirm("Proceed?", yes)
    js = http_json("POST", f"{base}/zones/{zid}/dns_records", token, payload=payload)
    r = js["result"]
    print(f"Record {r.get('name')} ({r.get('type')}) created: {_summarize(r)} (ttl={r.get('ttl')} proxied={r.get('proxied')})")

def cmd_update(token, zone, rid, name, rtype, content, ttl, proxied, yes, base, **extra):
    zid = zone_id(token, zone, base)
    rtype = rtype.upper() if rtype else None
    rid, current = find_record(token, zid, rid, name, rtype, zone, base)
    new_name = normalize_name(zone, name) if name else current.get("name")
    new_type = rtype or current.get("type")
    new_ttl = ttl if ttl is not None else current.get("ttl")
    prox_eff = proxied if proxied is not None else (bool(current.get("proxied")) if isinstance(current.get("proxied"), bool) else None)

    cur_data = current.get("data") or {}
    if new_type == "MX":
        new_content = content if content is not None else current.get("content")
        eff_priority = extra.get("priority") if extra.get("priority") is not None else current.get("priority")
        payload = build_payload(new_name, new_type, new_content, new_ttl, prox_eff, priority=eff_priority)
    elif new_type == "SRV":
        payload = build_payload(new_name, new_type, None, new_ttl, prox_eff,
            priority=extra.get("priority") if extra.get("priority") is not None else cur_data.get("priority"),
            weight=extra.get("weight") if extra.get("weight") is not None else cur_data.get("weight"),
            port=extra.get("port") if extra.get("port") is not None else cur_data.get("port"),
            target=extra.get("target") if extra.get("target") is not None else cur_data.get("target"),
        )
    elif new_type == "CAA":
        payload = build_payload(new_name, new_type, None, new_ttl, prox_eff,
            caa_flags=extra.get("caa_flags") if extra.get("caa_flags") is not None else cur_data.get("flags"),
            caa_tag=extra.get("caa_tag") if extra.get("caa_tag") is not None else cur_data.get("tag"),
            caa_value=extra.get("caa_value") if extra.get("caa_value") is not None else cur_data.get("value"),
        )
    else:
        new_content = content if content is not None else current.get("content")
        payload = build_payload(new_name, new_type, new_content, new_ttl, prox_eff)

    preview = {"type": new_type, "content": payload.get("content"),
               "priority": payload.get("priority"), "data": payload.get("data")}
    print("About to UPDATE:")
    print(f"from: {current.get('type')} {current.get('name')} → {_summarize(current)}")
    print(f"to:   {new_type} {new_name} → {_summarize(preview)}")
    confirm("Proceed?", yes)
    js = http_json("PUT", f"{base}/zones/{zid}/dns_records/{rid}", token, payload=payload)
    r = js["result"]
    print(f"Record {r.get('name')} ({r.get('type')}) updated: {_summarize(current)} → {_summarize(r)}")

def cmd_delete(token, zone, rid, name, rtype, yes, base):
    zid = zone_id(token, zone, base)
    rtype = rtype.upper() if rtype else None
    rid, current = find_record(token, zid, rid, name, rtype, zone, base)
    print(f"About to DELETE: {current.get('type')} {current.get('name')} → {_summarize(current)}")
    confirm("Proceed?", yes)
    http_json("DELETE", f"{base}/zones/{zid}/dns_records/{rid}", token)
    print(f"Record {current.get('name')} ({current.get('type')}) deleted: {_summarize(current)}")

def main():
    ap = argparse.ArgumentParser(description="Cloudflare DNS CLI (stdlib only)", formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--token-file", help="Read API token from file (first line). If omitted, uses CF_API_TOKEN.")
    ap.add_argument("--base-url", default=API_BASE, help="Override API base URL (default: %(default)s).")
    ap.add_argument("--yes", action="store_true", help="Skip confirmation prompts for add/update/delete.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("zones", help="List all zones accessible to the token")

    pl = sub.add_parser("list", help="List DNS records in a zone")
    pl.add_argument("zone", help="Zone name (e.g. example.com)")
    pl.add_argument("--type", dest="rtype", help="Filter by record type (A, AAAA, CNAME, MX, TXT, SRV, CAA, NS, ...)")
    pl.add_argument("--name-substr", help="Filter by substring of record name (case-insensitive)")
    pl.add_argument("--json", action="store_true", help="Output raw JSON instead of table")

    pa = sub.add_parser("add", help="Create DNS record")
    pa.add_argument("zone", help="Zone name (e.g. example.com)")
    pa.add_argument("--name", required=True, help="Record name; '@' or zone name for apex, short name auto-suffixed with zone")
    pa.add_argument("--type", dest="rtype", required=True, help="Record type: A, AAAA, CNAME, MX, TXT, SRV, CAA, NS, ...")
    pa.add_argument("--content", help="Record content (IP, hostname, text). Required for most types; for MX it's the mail server. Not used for SRV/CAA (use data fields)")
    pa.add_argument("--ttl", type=int, help="TTL in seconds (1 = auto, default = auto)")
    pa.add_argument("--proxied", choices=["on","off"], help="Cloudflare proxy (A/AAAA/CNAME only)")
    pa.add_argument("--priority", type=int, help="MX/SRV priority (0-65535)")
    pa.add_argument("--weight", type=int, help="SRV weight (0-65535)")
    pa.add_argument("--port", type=int, help="SRV port (1-65535)")
    pa.add_argument("--target", help="SRV target hostname")
    pa.add_argument("--caa-flags", dest="caa_flags", type=int, help="CAA flags: 0 (default) or 128 (critical)")
    pa.add_argument("--caa-tag", dest="caa_tag", choices=["issue","issuewild","iodef"], help="CAA tag")
    pa.add_argument("--caa-value", dest="caa_value", help="CAA value (e.g. 'letsencrypt.org' or 'mailto:abuse@example.com')")

    pu = sub.add_parser("update", help="Update DNS record (merges with current values)")
    pu.add_argument("zone", help="Zone name")
    pu.add_argument("--id", dest="rid", help="Record ID (skip lookup by name+type)")
    pu.add_argument("--name", help="Record name (used with --type to find record if --id not given)")
    pu.add_argument("--type", dest="rtype", help="Record type (used with --name)")
    pu.add_argument("--content", help="New content")
    pu.add_argument("--ttl", type=int, help="New TTL in seconds (1 = auto)")
    pu.add_argument("--proxied", choices=["on","off"], help="Toggle Cloudflare proxy")
    pu.add_argument("--priority", type=int, help="New MX/SRV priority")
    pu.add_argument("--weight", type=int, help="New SRV weight")
    pu.add_argument("--port", type=int, help="New SRV port")
    pu.add_argument("--target", help="New SRV target")
    pu.add_argument("--caa-flags", dest="caa_flags", type=int, help="New CAA flags")
    pu.add_argument("--caa-tag", dest="caa_tag", choices=["issue","issuewild","iodef"], help="New CAA tag")
    pu.add_argument("--caa-value", dest="caa_value", help="New CAA value")

    pd = sub.add_parser("delete", help="Delete DNS record")
    pd.add_argument("zone", help="Zone name")
    pd.add_argument("--id", dest="rid", help="Record ID")
    pd.add_argument("--name", help="Record name (used with --type if --id not given)")
    pd.add_argument("--type", dest="rtype", help="Record type")

    if len(sys.argv) == 1:
        print("""Cloudflare DNS CLI (stdlib only)

Usage:
  cf-dns.py zones
  cf-dns.py list <zone> [--type TYPE] [--name-substr STR] [--json]
  cf-dns.py add <zone> --name NAME --type TYPE [--content VALUE] [--ttl N] [--proxied on|off]
                       [MX:  --priority N]
                       [SRV: --priority N --weight N --port N --target HOST]
                       [CAA: --caa-tag issue|issuewild|iodef --caa-value VAL [--caa-flags 0|128]]
  cf-dns.py update <zone> (--id ID | --name NAME --type TYPE) [--content VALUE] [--ttl N] [--proxied on|off]
                       [--priority N] [--weight N] [--port N] [--target HOST]
                       [--caa-flags N] [--caa-tag TAG] [--caa-value VAL]
  cf-dns.py delete <zone> (--id ID | --name NAME --type TYPE)

Examples:
  cf-dns.py zones | column -t -s$'\t'
  cf-dns.py list example.com | column -t -s$'\t'
  cf-dns.py add example.com --name www --type A --content 203.0.113.10 --ttl 300 --proxied on
  cf-dns.py add example.com --name @ --type MX --content mail.example.com --priority 10
  cf-dns.py add example.com --name _sip._tcp --type SRV --priority 10 --weight 5 --port 5060 --target sip.example.com
  cf-dns.py add example.com --name @ --type CAA --caa-tag issue --caa-value "letsencrypt.org"
  cf-dns.py update example.com --name @ --type MX --priority 20
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
        extra = {"priority": args.priority, "weight": args.weight, "port": args.port,
                 "target": args.target, "caa_flags": args.caa_flags,
                 "caa_tag": args.caa_tag, "caa_value": args.caa_value}
        cmd_add(token, args.zone, args.name, args.rtype, args.content, args.ttl, prox, args.yes, base, **extra)
    elif args.cmd == "update":
        prox = None
        if args.proxied is not None: prox = (args.proxied.lower()=="on")
        extra = {"priority": args.priority, "weight": args.weight, "port": args.port,
                 "target": args.target, "caa_flags": args.caa_flags,
                 "caa_tag": args.caa_tag, "caa_value": args.caa_value}
        cmd_update(token, args.zone, args.rid, args.name, args.rtype, args.content, args.ttl, prox, args.yes, base, **extra)
    elif args.cmd == "delete":
        cmd_delete(token, args.zone, args.rid, args.name, args.rtype, args.yes, base)

if __name__ == "__main__":
    main()
