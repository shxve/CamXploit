import argparse
import base64
import hashlib
import ipaddress
import os
import re
import socket
import ssl
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning

try:
    from defusedxml import ElementTree as ET  # type: ignore
    _XML_HARDENED = True
except ImportError:
    from xml.etree import ElementTree as ET  # noqa: N813
    _XML_HARDENED = False

# Suppress SSL warnings — the tool intentionally accepts self-signed camera certs
warnings.filterwarnings("ignore", message="Unverified HTTPS request")
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

if sys.stdout.isatty():
    R = '\033[31m'  # Red
    G = '\033[32m'  # Green
    C = '\033[36m'  # Cyan
    W = '\033[0m'   # Reset
    Y = '\033[33m'  # Yellow
    M = '\033[35m'  # Magenta
    B = '\033[34m'  # Blue
else:
    R = G = C = W = Y = M = B = ''  # No color in non-TTY environments

BANNER = rf"""
{R}⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣸⣏⠛⠻⠿⣿⣶⣤⣄⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⣿⣿⣿⣷⣦⣤⣈⠙⠛⠿⣿⣷⣶⣤⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⢸⣿⣿⣿⣿⣿⣿⣿⣿⣶⣦⣄⣈⠙⠻⠿⣿⣷⣶⣤⣀⡀⠀⠀⠀⠀⠀⠀
⠀⠀⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣦⣄⡉⠛⠻⢿⣿⣷⣶⣤⣀⠀⠀
⠀⠀⠀⠉⠙⠛⠿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣾⢻⣍⡉⠉⣿⠇⠀
⠀⠀⠀⠀⠀⠀⠀⢹⡏⢹⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠇⣰⣿⣿⣾⠏⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠘⣿⠈⣿⠸⣯⠉⠛⠿⢿⣿⣿⣿⣿⡏⠀⠻⠿⣿⠇⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢿⡆⢻⡄⣿⡀⠀⠀⠀⠈⠙⠛⠿⠿⠿⠿⠛⠋⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⢸⣧⠘⣇⢸⣇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⣀⣀⣿⣴⣿⢾⣿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⣴⡶⠾⠟⠛⠋⢹⡏⠀⢹⡇⣿⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⢠⣿⠀⠀⠀⠀⢀⣈⣿⣶⠿⠿⠛⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⢸⣿⣴⠶⠞⠛⠉⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀

  {G}[💀] CamXploit - Camera Exploitation & Exposure Scanner
  {C}[🔍] Discover open CCTV cameras & security flaws
  {Y}[⚠️] For educational & security research purposes only!{W}

  {B}VERSION{W}  = 2.0.2
  {B}Made By{W}  = Spyboy
  {B}Twitter{W}  = https://spyboy.in/twitter
  {B}Discord{W}  = https://spyboy.in/Discord
  {B}Github{W}   = https://github.com/spyboy-productions/CamXploit
"""

# Ports commonly used by IP cameras and CCTV devices.
# Built from named groups so it's obvious what's in scope, then deduplicated
# and sorted. The previous version was ~90 lines of hand-typed integers with
# overlapping groups; this collapses to the same set via range().
def _build_common_ports():
    ports = set()

    # Standard web / TLS. Preserves the exact set from the previous version:
    # only 8000, 8001, 8008 in the low 8000s; 8080-8099 in the block above 8080.
    ports.update(range(80, 90))           # 80-89 (HTTP + alt)
    ports.add(443)                        # HTTPS
    ports.update({8000, 8001, 8008, 8080})
    ports.update(range(8081, 8100))       # 8081-8099
    ports.update(range(8100, 8200, 10))   # 8100, 8110, ..., 8190 (VLC streaming)
    ports.add(8443)                       # HTTPS alt
    ports.update(range(8888, 8900))       # 8888-8899

    # RTSP: standard + X554 alt ports commonly seen on OEM gear
    ports.add(554)
    ports.update({p for p in (1554, 2554, 3554, 4554, 5554, 6554, 7554, 8554, 9554, 10554)})

    # RTMP + MMS
    ports.update(range(1935, 1940))       # 1935-1939
    ports.update(range(1755, 1761))       # 1755-1760

    # Vendor-specific / DVR
    ports.update(range(37777, 37801))     # 37777-37800 (Dahua, misc DVR)

    # ONVIF discovery
    ports.update(range(3702, 3711))       # 3702-3710

    # Well-known non-camera services (kept for completeness — some DVRs
    # co-locate FTP/Telnet on the same host). Consider moving behind an
    # --extra-ports flag in a future revision.
    ports.update({21, 22, 23, 25, 53, 110, 143, 993, 995})

    # Alt-port bands. The old list had a mix — X000..X005 for 2000/3000/4000
    # and X000..X010 for 5000/6000/7000/9000 — kept as-is to avoid silently
    # widening the scan surface.
    for base in (2000, 3000, 4000):
        ports.update(range(base, base + 6))
    for base in (5000, 6000, 7000, 9000):
        ports.update(range(base, base + 11))
    ports.update(range(1024, 1031))       # 1024-1030

    # "9990–9999" range (was written high-to-low in the old list)
    ports.update(range(9990, 10000))

    # High-port bands: X000..X010 for X in {10..15, 20..65} except 26..29
    # which the original list intentionally omitted.
    high_bands = list(range(10, 16)) + list(range(20, 26)) + list(range(30, 66))
    for x in high_bands:
        base = x * 1000
        ports.update(range(base, base + 11))

    return sorted(ports)


COMMON_PORTS = _build_common_ports()

# Best‑effort mapping of common CCTV / streaming ports to service names
PORT_SERVICE_MAP = {
    # Web interfaces
    80:  ("HTTP", "Web Interface"),
    81:  ("HTTP-Alt", "Web Interface"),
    82:  ("HTTP-Alt", "Web Interface"),
    83:  ("HTTP-Alt", "Web Interface"),
    84:  ("HTTP-Alt", "Web Interface"),
    85:  ("HTTP-Alt", "Web Interface"),
    86:  ("HTTP-Alt", "Web Interface"),
    87:  ("HTTP-Alt", "Web Interface"),
    88:  ("HTTP-Alt", "Web Interface"),
    89:  ("HTTP-Alt", "Web Interface"),
    443: ("HTTPS", "Secure Web Interface"),
    8080: ("HTTP-Alt", "Web Interface"),
    8443: ("HTTPS-Alt", "Secure Web Interface"),
    8000: ("HTTP-Alt", "Web Interface / Hikvision"),
    8001: ("HTTP-Alt", "Web Interface"),
    8888: ("HTTP-Alt", "Web Interface"),
    9000: ("HTTP-Alt", "Web Interface"),

    # RTSP / RTMP streaming
    554: ("RTSP", "Real-Time Streaming Protocol"),
    8554: ("RTSP-Alt", "Real-Time Streaming Protocol"),
    10554: ("RTSP-Alt", "Real-Time Streaming Protocol"),
    1935: ("RTMP", "Real-Time Messaging Protocol (Streaming)"),

    # ONVIF / discovery
    3702: ("ONVIF", "Device Discovery / Control"),

    # Vendor‑specific / DVR ports
    37777: ("Dahua", "DVR/NVR Service"),
    37778: ("Dahua", "DVR/NVR Service"),
    8008:  ("Hikvision", "Web / API"),

    # MMS / legacy streaming
    1755: ("MMS", "Microsoft Media Server"),
}

# Common admin login pages or interesting paths for cameras
COMMON_PATHS = [
    "/", "/admin", "/login", "/viewer", "/webadmin", "/video", "/stream", "/live", "/snapshot", "/onvif-http/snapshot",
    "/system.ini", "/config", "/setup", "/cgi-bin/", "/api/", "/camera", "/img/main.cgi"
]

# Default credentials commonly used in IP cameras / DVR / NVR
# This is intentionally broad and contains combinations seen across
# Hikvision, Dahua, Axis, CP Plus, Uniview, generic OEM DVRs, etc.
DEFAULT_CREDENTIALS = {
    # Very common admin-style accounts
    "admin": [
        "admin", "1234", "12345", "123456", "1234567", "12345678", "123456789",
        "admin123", "admin1234", "admin12345",
        "password", "pass", "123", "1111", "0000", "8888",
        "default", "admin@123", "Admin123", "Admin1234",
        "888888", "666666",  # Common on many DVRs (Hikvision, Dahua, OEM)
        "4321", "9999"
    ],

    # Root-style accounts (Linux‑based firmwares, some NVRs)
    "root": [
        "root", "toor", "1234", "12345", "123456",
        "pass", "password", "root123", "admin", "1111", "0000"
    ],

    # Generic user accounts
    "user": [
        "user", "user123", "password", "1234", "12345", "123456"
    ],
    "guest": [
        "guest", "guest123", "1234", "12345", "123456"
    ],
    "operator": [
        "operator", "operator123", "1234", "12345"
    ],

    # Additional usernames seen on various CCTV brands / OEM NVRs
    "administrator": [
        "administrator", "admin", "1234", "12345", "123456", "password"
    ],
    "supervisor": [
        "supervisor", "1234", "12345", "123456", "password"
    ],
    "support": [
        "support", "support123", "1234", "password"
    ],
    "system": [
        "system", "system123", "1234", "12345", "123456"
    ],
    "viewer": [
        "viewer", "viewer123", "1234", "12345"
    ],
    "admin1": [
        "admin", "admin1", "1234", "12345", "123456", "password"
    ],
    # Some devices expose numeric "admin"‑like users
    "888888": [
        "888888", "123456", "000000"
    ],
    "666666": [
        "666666", "123456", "000000"
    ],
}

# Ports for which we default to HTTPS; a TLS probe supersedes this list at
# runtime (see get_protocol / probe_tls).
HTTPS_PORTS = {443, 8443, 8444, 9443, 4433, 7443}
HEADERS = {
    'User-Agent': 'CamXploit/2.1 (+https://github.com/spyboy-productions/CamXploit)'
}
TIMEOUT = 5
PORT_SCAN_TIMEOUT = 1.5

# Hard cap on HTTP response bodies we will read from an untrusted device.
# Defends check_if_camera / fingerprint_* from OOM against a hostile target.
MAX_BODY_BYTES = 256 * 1024  # 256 KiB
MAX_XML_BYTES  = 64 * 1024   # 64 KiB — for camera config XML

# Serialise console output across worker threads so lines don't interleave.
print_lock = threading.Lock()
_orig_print = print

def print(*args, **kwargs):  # noqa: A001 - intentional shadow, thread-safe wrapper
    with print_lock:
        _orig_print(*args, **kwargs)

# Runtime configuration populated by argparse in main(). Kept as a module-level
# object so worker threads can consult it without extra plumbing.
class _RuntimeConfig:
    authorised = False   # user asserted authorisation for active probing
    no_brute = False     # skip credential testing entirely
    no_osint = False     # skip third-party OSINT lookups (ipinfo, Shodan links)
    threads = 100        # port-scan concurrency
    _tls_cache = {}      # (ip, port) -> bool

CONFIG = _RuntimeConfig()


def _make_session(pool_size=64):
    """Shared HTTP session with connection pooling and no auto-retries.

    Previous versions created a fresh TCP+TLS connection per request; against
    a device with many ports open that's thousands of handshakes. A pooled
    session reuses connections per (host, port).
    """
    s = requests.Session()
    s.headers.update(HEADERS)
    adapter = HTTPAdapter(pool_connections=pool_size,
                          pool_maxsize=pool_size,
                          max_retries=0)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


SESSION = _make_session()

# Curated CVE database — only entries manually verified against NVD for the
# named vendor. Previous versions of this file listed CVE-2021-31955..31964
# under Hikvision; those are Windows-kernel CVEs and were removed.
CVE_DATABASE = {
    "hikvision": [
        "CVE-2021-36260",  # unauth RCE, IPC firmware
        "CVE-2017-7921",   # auth bypass in ISAPI
    ],
    "dahua": [
        "CVE-2021-33044",  # ID/password auth bypass
        "CVE-2022-30563",  # ONVIF replay auth bypass
    ],
    "axis": [
        "CVE-2018-10660",  # shell injection in .srv scripts
    ],
    "cp plus": [],  # No verified CVEs at time of writing
}

# Thread control
threads_running = True

def print_search_urls(ip):
    print(f"\n[🌍] {C}Use these URLs to check the camera exposure manually:{W}")
    print(f"  🔹 Shodan: https://www.shodan.io/search?query={ip}")
    print(f"  🔹 Censys: https://search.censys.io/hosts/{ip}")
    print(f"  🔹 Zoomeye: https://www.zoomeye.org/searchResult?q={ip}")
    print(f"  🔹 Google Dorking (Quick Search): https://www.google.com/search?q=site:{ip}+inurl:view/view.shtml+OR+inurl:admin.html+OR+inurl:login")

def google_dork_search(ip):
    print(f"\n[🔎] {C}Google Dorking Suggestions:{W}")
    queries = [
        f"site:{ip} inurl:view/view.shtml",
        f"site:{ip} inurl:admin.html",
        f"site:{ip} inurl:login",
        f"intitle:'webcam' inurl:{ip}",
    ]
    for q in queries:
        print(f"  🔍 Google Dork: https://www.google.com/search?q={q.replace(' ', '+')}")

def get_ip_location_info(ip):
    """Get comprehensive IP and location information"""
    if CONFIG.no_osint:
        return
    print(f"\n{C}[🌍] IP and Location Information:{W}")
    try:
        response = SESSION.get(f"https://ipinfo.io/{ip}/json", timeout=TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            
            # Basic IP Information
            print(f"  🔍 IP: {data.get('ip', 'N/A')}")
            print(f"  🏢 ISP: {data.get('org', 'N/A')}")
            
            # Location Information
            if 'loc' in data:
                lat, lon = data['loc'].split(',')
                print(f"\n  📍 Coordinates:")
                print(f"    Latitude: {lat}")
                print(f"    Longitude: {lon}")
                print(f"    🔗 Google Maps: https://www.google.com/maps?q={lat},{lon}")
                print(f"    🔗 Google Earth: https://earth.google.com/web/@{lat},{lon},0a,1000d,35y,0h,0t,0r")
            
            # Geographic Information
            print(f"\n  🌎 Geographic Details:")
            print(f"    City: {data.get('city', 'N/A')}")
            print(f"    Region: {data.get('region', 'N/A')}")
            print(f"    Country: {data.get('country', 'N/A')}")
            print(f"    Postal Code: {data.get('postal', 'N/A')}")
            
            # Timezone Information
            if 'timezone' in data:
                print(f"\n  ⏰ Timezone: {data['timezone']}")
            
        else:
            print(f"{R}[!] Failed to fetch IP information.{W}")
    except Exception as e:
        print(f"{R}[!] Error getting IP information: {str(e)}{W}")

def parse_ip_port(input_str):
    """Parse IP or IP:PORT. IPv4 only.

    Accepts:
        "1.2.3.4"       -> ("1.2.3.4", None)
        "1.2.3.4:8080"  -> ("1.2.3.4", 8080)
    Rejects IPv6 explicitly (bracketed forms like [::1]:80 are also rejected;
    scanning IPv6 targets is out of scope for this version).
    """
    input_str = input_str.strip()

    # Explicitly reject IPv6 forms so the split-on-colon heuristic below can't
    # silently mangle "fe80::1" into ("fe80:", 1). This covers bracketed and
    # bare IPv6 addresses.
    if input_str.startswith('[') or input_str.count(':') > 1:
        print(f"{R}[!] IPv6 targets are not supported in this version.{W}")
        return None, None

    if ':' in input_str:
        ip_str, _, port_str = input_str.rpartition(':')
        try:
            port = int(port_str)
        except ValueError:
            print(f"{R}[!] Invalid port number: {port_str}{W}")
            return None, None
        if not (1 <= port <= 65535):
            print(f"{R}[!] Invalid port number. Must be between 1-65535{W}")
            return None, None
        return ip_str.strip(), port

    return input_str, None

def validate_ip(target_ip):
    """Validate IPv4 address."""
    try:
        ip = ipaddress.ip_address(target_ip)
    except ValueError:
        print(f"{R}[!] Invalid IP address format{W}")
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        print(f"{R}[!] IPv6 targets are not supported in this version.{W}")
        return False
    if ip.is_private:
        print(f"{Y}[⚠️] Private IP address detected. Continuing (make sure you own this network).{W}")
    if ip.is_loopback or ip.is_link_local or ip.is_multicast:
        print(f"{Y}[⚠️] Special-use address ({ip}); OSINT lookups will be skipped.{W}")
    return True

def probe_tls(ip, port, timeout=1.5):
    """Best-effort TLS probe. Cached per (ip, port).

    Attempts a TLS handshake; if it succeeds the port is treated as HTTPS.
    Falls back cleanly on any error and returns False, meaning "use HTTP".
    """
    key = (ip, port)
    if key in CONFIG._tls_cache:
        return CONFIG._tls_cache[key]
    result = False
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=ip) as tls:
                # tls.version() returns e.g. 'TLSv1.2' on success
                result = tls.version() is not None
    except Exception:
        result = False
    CONFIG._tls_cache[key] = result
    return result

def get_protocol(port, ip=None):
    """Return 'https' or 'http'. Uses TLS probe when we have an IP.

    Kept backwards-compatible: callers that pass only a port still get the
    static allow-list behaviour.
    """
    if port in HTTPS_PORTS:
        return "https"
    if ip is not None and probe_tls(ip, port):
        return "https"
    return "http"

def _capped_get(url, cap=MAX_BODY_BYTES, **kwargs):
    """GET `url` and return (response, body_text) with the body capped at `cap`
    bytes.

    The target device is untrusted: a plain ``requests.get`` eagerly downloads
    the whole body into memory, so a hostile server can hand us an unbounded
    response and OOM the scanner. This streams the body and stops at `cap`.
    Session defaults (HEADERS, TIMEOUT, verify=False) are applied unless the
    caller overrides them. Status code and headers remain readable on the
    returned response after the body stream is closed.
    """
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", TIMEOUT)
    kwargs.setdefault("verify", False)
    kwargs["stream"] = True
    with SESSION.get(url, **kwargs) as resp:
        buf = bytearray()
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) >= cap:
                break
        return resp, bytes(buf).decode("utf-8", errors="ignore")

def _rtsp_request(ip, port, verb, path="/", headers=None, timeout=2.0):
    """Send one RTSP request and return the raw response bytes (or None)."""
    hdr_lines = ""
    if headers:
        hdr_lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    req = (
        f"{verb} rtsp://{ip}:{port}{path} RTSP/1.0\r\n"
        f"CSeq: 1\r\n"
        f"User-Agent: CamXploit/2.1\r\n"
        f"{hdr_lines}"
        f"\r\n"
    ).encode("ascii", errors="ignore")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex((ip, port)) != 0:
                return None
            s.sendall(req)
            chunks = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                # RTSP responses fit comfortably in a few KB; stop early on
                # end-of-headers to keep the probe snappy.
                if b"\r\n\r\n" in b"".join(chunks):
                    break
            return b"".join(chunks) if chunks else None
    except Exception:
        return None


def probe_rtsp(ip, port):
    """RTSP detection via an OPTIONS request.

    Requires the response to *start with* an RTSP status line and to advertise
    common RTSP verbs (via Public: header or the response body). This is
    stricter than the previous "any RTSP/1.0 substring anywhere" check, which
    could match on binary junk from unrelated services.

    Uses a 3s timeout (vs. PORT_SCAN_TIMEOUT=1.5s for the TCP-connect probe).
    RTSP servers are often slower to respond than a bare TCP accept — under
    the ~200-thread scan load a 1.5s window intermittently missed real RTSP
    services on internet targets.
    """
    data = _rtsp_request(ip, port, "OPTIONS", timeout=3.0)
    if not data:
        return False
    # RTSP status line must be the very first thing on the wire.
    if not data.startswith(b"RTSP/1.0 ") and not data.startswith(b"RTSP/2.0 "):
        return False
    text = data.decode("ascii", errors="ignore")
    # Split off the status line + headers.
    head = text.split("\r\n\r\n", 1)[0]
    if not re.search(r"^RTSP/[12]\.0\s+\d{3}\s", head, re.MULTILINE):
        return False
    return any(v in head for v in ("Public:", "DESCRIBE", "SETUP", "PLAY", "TEARDOWN"))


def _parse_digest_challenge(header_val):
    """Parse a WWW-Authenticate: Digest header into a dict of parameters."""
    if not header_val or not header_val.lower().lstrip().startswith("digest"):
        return None
    params = {}
    # Naive but sufficient for camera challenges: key="value" pairs.
    for m in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s]+))', header_val):
        params[m.group(1).lower()] = m.group(2) if m.group(2) is not None else m.group(3)
    return params or None


def _digest_response(user, password, method, uri, challenge):
    """Compute a minimal RFC 2617 Digest response header value."""
    realm  = challenge.get("realm", "")
    nonce  = challenge.get("nonce", "")
    qop    = challenge.get("qop")
    algo   = (challenge.get("algorithm") or "MD5").upper()
    opaque = challenge.get("opaque")

    def _h(s):
        return hashlib.md5(s.encode("utf-8", "ignore")).hexdigest()

    ha1 = _h(f"{user}:{realm}:{password}")
    ha2 = _h(f"{method}:{uri}")
    if qop:
        # qop=auth uses cnonce + nc; single-shot values are fine here.
        cnonce = hashlib.md5(os.urandom(8)).hexdigest()[:16]
        nc = "00000001"
        # qop may be a comma list like "auth,auth-int" — pick "auth".
        qop_pick = "auth"
        resp = _h(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop_pick}:{ha2}")
        parts = [
            f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"',
            f'uri="{uri}"', f'algorithm={algo}', f'response="{resp}"',
            f'qop={qop_pick}', f'nc={nc}', f'cnonce="{cnonce}"',
        ]
    else:
        resp = _h(f"{ha1}:{nonce}:{ha2}")
        parts = [
            f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"',
            f'uri="{uri}"', f'algorithm={algo}', f'response="{resp}"',
        ]
    if opaque:
        parts.append(f'opaque="{opaque}"')
    return "Digest " + ", ".join(parts)

def check_ports(ip, additional_ports=None):
    """
    Scan ports on target IP.
    
    Args:
        ip: Target IP address
        additional_ports: Optional list of additional ports to scan (e.g., user-specified ports)
    
    Returns:
        tuple: (open_ports, rtsp_ports)
    """
    # Combine COMMON_PORTS with any additional ports
    ports_to_scan = list(COMMON_PORTS)
    if additional_ports:
        for port in additional_ports:
            if port not in ports_to_scan:
                ports_to_scan.append(port)
    
    print(f"\n[🔍] {C}Scanning comprehensive CCTV ports on IP:{W}", ip)
    print(f"{Y}[⚠️] This will scan {len(ports_to_scan)} ports. This may take a while...{W}")
    open_ports = []
    rtsp_ports = []
    lock = threading.Lock()
    scanned_count = 0
    total = len(ports_to_scan)

    def scan_port(port):
        """Return (port, is_open, is_rtsp) — printing is deferred to the caller."""
        if not threads_running:
            return port, False, False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(PORT_SCAN_TIMEOUT)
                if sock.connect_ex((ip, port)) != 0:
                    return port, False, False
        except Exception:
            return port, False, False
        # Probe RTSP outside the connect socket so a slow RTSP handshake
        # doesn't hold a connection open longer than needed.
        return port, True, probe_rtsp(ip, port)

    # Real thread pool — completed futures free their slot immediately, so a
    # single slow port can't stall the scan the way the old batch-of-100
    # thread.join() loop did.
    with ThreadPoolExecutor(max_workers=CONFIG.threads) as pool:
        for future in as_completed(pool.submit(scan_port, p) for p in ports_to_scan):
            if not threads_running:
                break
            port, is_open, is_rtsp = future.result()
            with lock:
                scanned_count += 1
                if is_open:
                    open_ports.append(port)
                    if is_rtsp:
                        rtsp_ports.append(port)
                        service_name = "RTSP"
                        service_desc = "Real-Time Streaming Protocol" if port == 554 else "Non-standard port"
                    else:
                        service_name, service_desc = PORT_SERVICE_MAP.get(port, ("Unknown Service", ""))
                    service_str = f"{service_name}  ({service_desc})" if service_desc else service_name
                    print(f"  ✅ [OPEN] {port}/tcp  {service_str}")
                    if is_rtsp:
                        print(f"      Stream URL: rtsp://{ip}:{port}/")
                if scanned_count % 50 == 0:
                    print(f"  📊 Scanned {scanned_count}/{total} ports...")

    print(f"\n{Y}[📊] Scan completed: {scanned_count} ports checked, {len(open_ports)} ports open{W}")
    return sorted(open_ports), sorted(rtsp_ports)

def check_if_camera(ip, open_ports, rtsp_ports=None):
    """Enhanced camera detection with detailed port analysis.

    `rtsp_ports` (from check_ports) lets us skip HTTP analysis on ports we
    already know speak RTSP — a Dahua RTSP server on 554 will close a plain
    HTTP GET, producing a noisy "Connection Error" that doesn't tell the
    operator anything they don't already know.
    """
    rtsp_ports = set(rtsp_ports or [])
    print(f"\n{C}[📷] Analyzing Ports for Camera Indicators:{W}")
    camera_indicators = False
    
    # Common camera server headers and keywords
    camera_servers = {
        'hikvision': ['hikvision', 'dvr', 'nvr'],
        'dahua': ['dahua', 'dvr', 'nvr'],
        'axis': ['axis', 'axis communications'],
        'sony': ['sony', 'ipela'],
        'bosch': ['bosch', 'security systems'],
        'samsung': ['samsung', 'samsung techwin'],
        'panasonic': ['panasonic', 'network camera'],
        'vivotek': ['vivotek', 'network camera'],
        'cp plus': ['cp plus', 'cp-plus', 'cpplus', 'cp_plus'],
        'generic': ['camera', 'webcam', 'surveillance', 'ip camera', 'network camera', 'dvr', 'nvr', 'recorder']
    }
    
    # Common camera content types
    camera_content_types = [
        'image/jpeg',
        'image/mjpeg',
        'video/mpeg',
        'video/mp4',
        'video/h264',
        'application/x-mpegURL',
        'video/MP2T',
        'application/octet-stream',
        'text/html',
        'application/json'
    ]
    
    def analyze_port(port):
        nonlocal camera_indicators
        # Skip HTTP analysis on ports we know speak RTSP — either because the
        # port-scan probe positively identified them, or because they're in
        # the static PORT_SERVICE_MAP as RTSP (belt-and-braces: probe_rtsp can
        # time out under scan load on internet targets, and we don't want to
        # spam "Connection Error" for that).
        static_svc = PORT_SERVICE_MAP.get(port, ("", ""))[0].upper()
        if port in rtsp_ports or static_svc.startswith("RTSP"):
            print(f"\n  🔍 Port {port}: RTSP — skipping HTTP analysis.")
            return
        protocol = get_protocol(port, ip)
        base_url = f"{protocol}://{ip}:{port}"

        print(f"\n  🔍 Analyzing Port {port} ({protocol.upper()}):")

        # Check server headers and response
        try:
            response, body_text = _capped_get(base_url)
            server_header = response.headers.get('Server', '').lower()
            content_type = response.headers.get('Content-Type', '').lower()
            
            # Check server headers for camera brands
            brand_found = False
            for brand, keywords in camera_servers.items():
                if any(keyword in server_header for keyword in keywords):
                    print(f"    ✅ {brand.upper()} Camera Server Detected")
                    brand_found = True
                    camera_indicators = True
                    break
            
            # Content-type check: only flag types that are actually
            # camera-specific. text/html and application/json were previously
            # in this list and made every HTTP server "look like a camera".
            camera_content_types_strict = (
                'image/jpeg', 'image/mjpeg', 'multipart/x-mixed-replace',
                'video/', 'application/x-mpegurl', 'application/vnd.apple.mpegurl',
            )
            if any(ct in content_type for ct in camera_content_types_strict):
                print(f"    ✅ Camera Content Type: {content_type}")
                camera_indicators = True

            # Check response content for camera indicators
            content = body_text.lower() if response.status_code == 200 else ''
            if content:
                camera_keywords = ['camera', 'webcam', 'surveillance', 'stream', 'video',
                                   'snapshot', 'dvr', 'nvr', 'recorder', 'cctv']
                found_keywords = [kw for kw in camera_keywords if kw in content]
                if found_keywords:
                    print(f"    ✅ Camera Keywords Found: {', '.join(found_keywords)}")
                    camera_indicators = True

                if any(x in content for x in ['cp plus', 'cp-plus', 'cpplus', 'cp_plus', 'uvr', '0401e1']):
                    print(f"    ✅ CP Plus Camera Detected!")
                    camera_indicators = True

            # Endpoint enumeration: only 200 with a camera-y content-type
            # counts as evidence. 401/403 are still reported (useful) but no
            # longer set the "found a camera" flag on their own.
            endpoints = ['/video', '/stream', '/snapshot', '/cgi-bin', '/admin',
                         '/viewer', '/login', '/index.html', '/']
            for endpoint in endpoints:
                try:
                    endpoint_url = f"{base_url}{endpoint}"
                    endpoint_response = SESSION.head(endpoint_url, headers=HEADERS,
                                                     timeout=TIMEOUT, verify=False,
                                                     allow_redirects=False)
                    code = endpoint_response.status_code
                    ep_ct = endpoint_response.headers.get('Content-Type', '').lower()
                    if code == 200 and any(t in ep_ct for t in camera_content_types_strict):
                        print(f"    ✅ Camera Endpoint (streaming content): {endpoint_url}")
                        camera_indicators = True
                    elif code in (401, 403):
                        # Note it, but do NOT flip camera_indicators — auth-
                        # required endpoints are true of many non-camera services.
                        print(f"    🔐 Auth-gated endpoint: {endpoint_url} (HTTP {code})")
                except (requests.exceptions.RequestException, Exception):
                    continue

            # Print server information
            if server_header:
                print(f"    ℹ️ Server: {server_header}")
            print(f"    ℹ️ Status Code: {response.status_code}")

            if response.status_code == 401:
                print(f"    🔐 Authentication Required")
                auth_type = response.headers.get('WWW-Authenticate', '')
                if auth_type:
                    print(f"    🔐 Auth Type: {auth_type}")

            # Title / login-form heuristics on the (already-capped) body.
            if content:
                if '<title>' in content:
                    title_start = content.find('<title>') + 7
                    title_end = content.find('</title>', title_start)
                    if title_end > title_start:
                        title = content[title_start:title_end].lower()
                        if any(x in title for x in ['dvr', 'nvr', 'recorder', 'surveillance', 'cctv', 'camera']):
                            print(f"    ✅ DVR/NVR Page Title: {title}")
                            camera_indicators = True

                # Login-form field detection: only flag if BOTH a username-ish
                # and password-ish field appear. "admin" alone matched on
                # practically every 200 page previously.
                has_user  = any(x in content for x in ['name="username"', 'name="user"', 'id="username"'])
                has_pass  = any(x in content for x in ['type="password"', 'name="password"', 'id="password"'])
                if has_user and has_pass:
                    print(f"    ✅ Login Form Detected")
                    camera_indicators = True

                if any(x in content for x in ['uvr-0401e1', 'uvr0401e1', '0401e1']):
                    print(f"    ✅ CP Plus UVR-0401E1 Model Detected!")
                    camera_indicators = True
            
        except requests.exceptions.RequestException as e:
            print(f"    ❌ Connection Error: {str(e)}")
        except Exception as e:
            print(f"    ❌ Error: {str(e)}")
    
    # Analyze each port
    for port in open_ports:
        analyze_port(port)
    
    return camera_indicators

def check_login_pages(ip, open_ports):
    print(f"\n[🔍] {C}Checking for authentication pages:{W}")

    def check_endpoint(port, path):
        if not threads_running:
            return None
        protocol = get_protocol(port, ip)
        url = f"{protocol}://{ip}:{port}{path}"
        try:
            response = SESSION.head(url, timeout=TIMEOUT, verify=False,
                                    allow_redirects=False)
            if response.status_code in (200, 401, 403):
                return (url, response.status_code)
        except (requests.exceptions.RequestException, Exception):
            pass
        return None

    tasks = [(port, path) for port in open_ports for path in COMMON_PATHS]
    found_urls = []
    with ThreadPoolExecutor(max_workers=min(50, len(tasks) or 1)) as pool:
        futures = [pool.submit(check_endpoint, port, path) for port, path in tasks]
        for future in as_completed(futures):
            if not threads_running:
                break
            result = future.result()
            if result:
                url, code = result
                found_urls.append(url)
                print(f"  ✅ Found login page: {url} (HTTP {code})")

    if not found_urls:
        print("  ❌ No authentication pages detected")
    else:
        print(f"  📊 Found {len(found_urls)} authentication pages")

def _rtsp_status(response_bytes):
    """Return the integer status code from an RTSP response, or None."""
    if not response_bytes:
        return None
    try:
        line = response_bytes.split(b"\r\n", 1)[0].decode("ascii", "ignore")
    except Exception:
        return None
    m = re.match(r"RTSP/[12]\.0\s+(\d{3})\s", line)
    return int(m.group(1)) if m else None


def _rtsp_www_authenticate(response_bytes):
    """Return the value of the WWW-Authenticate header, or None."""
    if not response_bytes:
        return None
    text = response_bytes.decode("ascii", "ignore")
    for line in text.split("\r\n"):
        if line.lower().startswith("www-authenticate:"):
            return line.split(":", 1)[1].strip()
    return None


def test_rtsp_credentials(ip, port, username, password, path="/"):
    """Try one username/password against an RTSP server using DESCRIBE.

    Rationale for DESCRIBE over OPTIONS: many cameras happily answer OPTIONS
    without any authentication (RFC 2326 does not require it for OPTIONS), so
    the previous implementation reported "success" for every valid RTSP
    endpoint regardless of the tried password.

    Supports both Basic and Digest challenges. Returns True only when the
    server responded 200 to an authenticated request.
    """
    uri = f"rtsp://{ip}:{port}{path}"

    # Step 1: unauthenticated DESCRIBE to discover the auth scheme.
    initial = _rtsp_request(ip, port, "DESCRIBE", path=path,
                            headers={"Accept": "application/sdp"})
    status = _rtsp_status(initial)
    if status is None:
        return False
    if status == 200:
        # Endpoint requires no auth at all — record as success, but flag it as
        # "no-auth" so the caller can tell "we guessed the password" from
        # "the camera is wide open".
        return True
    if status != 401:
        # 404, 405, 500… treat as inconclusive.
        return False

    challenge = _rtsp_www_authenticate(initial)
    if not challenge:
        return False

    # Step 2: authenticated DESCRIBE.
    if challenge.lower().startswith("basic"):
        b64 = base64.b64encode(f"{username}:{password}".encode("utf-8", "ignore")).decode()
        headers = {"Authorization": f"Basic {b64}",
                   "Accept": "application/sdp"}
    else:
        params = _parse_digest_challenge(challenge)
        if not params:
            return False
        headers = {"Authorization": _digest_response(username, password, "DESCRIBE", uri, params),
                   "Accept": "application/sdp"}

    resp = _rtsp_request(ip, port, "DESCRIBE", path=path, headers=headers)
    return _rtsp_status(resp) == 200

PRIORITY_CREDENTIALS = [
    ("admin", "admin"), ("admin", "1234"), ("admin", "12345"),
    ("admin", "123456"), ("admin", "password"), ("admin", ""),
    ("admin", "admin123"), ("admin", "888888"), ("admin", "666666"),
    ("root", "root"), ("root", "toor"), ("root", "1234"),
    ("admin", "1111"), ("admin", "0000"), ("admin", "8888"),
    ("user", "user"), ("guest", "guest"),
]

RTSP_PORTS_LIST = [554, 8554, 10554, 5554, 7070, 8555]
WEB_PORTS = [80, 443, 8080, 8443, 8000, 8001, 8008, 8081, 8082, 8888, 9000]

MAX_CREDENTIAL_TEST_TIME = 120   # total wall-clock budget across the whole call
CREDENTIAL_TIMEOUT = 2           # per-request timeout
_MAX_WEB_PORTS = 10              # cap for web-port breadth; logged when exceeded


def test_default_passwords(ip, open_ports, rtsp_ports=None):
    print(f"\n[🔑] {C}Testing common credentials:{W}")

    rtsp_ports = rtsp_ports or []
    all_rtsp_ports = sorted(set(rtsp_ports) | {p for p in open_ports if p in RTSP_PORTS_LIST})
    web_ports_all = [p for p in open_ports
                     if p in WEB_PORTS or (p < 10000 and p not in all_rtsp_ports)]
    web_ports = web_ports_all[:_MAX_WEB_PORTS]
    if len(web_ports_all) > _MAX_WEB_PORTS:
        print(f"{Y}[ℹ️] {len(web_ports_all) - _MAX_WEB_PORTS} web port(s) dropped "
              f"(cap {_MAX_WEB_PORTS}). Raise _MAX_WEB_PORTS to widen.{W}")

    if not all_rtsp_ports and not web_ports:
        print(f"{Y}[ℹ️] No ports found for credential testing{W}")
        return

    print(f"{Y}[ℹ️] Testing credentials on {len(all_rtsp_ports)} RTSP port(s) + "
          f"{len(web_ports)} web port(s)...{W}")
    if all_rtsp_ports:
        print(f"{C}[🎯] RTSP ports are prioritized (most important for CCTV cameras!){W}")

    start_time = time.time()
    found_event = threading.Event()   # cross-thread "stop" flag
    lock = threading.Lock()
    tested_count = [0]
    successes = []
    # (status_code, body_len, has_set_cookie) for the unauthenticated response
    # on each URL. Keyed by URL, populated on first probe.
    probe_cache = {}
    probe_lock = threading.Lock()
    skipped_urls = set()   # URLs where credential testing is meaningless

    def _time_left():
        return MAX_CREDENTIAL_TEST_TIME - (time.time() - start_time)

    def _bump(kind, label):
        with lock:
            tested_count[0] += 1
            if tested_count[0] % 20 == 0:
                print(f"  📊 Tested {tested_count[0]} credentials... "
                      f"({int(time.time() - start_time)}s elapsed) [{kind} {label}]")

    def _probe_unauth(url):
        """Fetch `url` once without credentials. Return (status, len, set_cookie)
        or None on error. Cached per URL — first caller pays the round-trip."""
        with probe_lock:
            if url in probe_cache:
                return probe_cache[url]
        try:
            r = SESSION.get(url, timeout=CREDENTIAL_TIMEOUT,
                            verify=False, allow_redirects=False)
            headers_lower = {k.lower() for k in r.headers.keys()}
            result = (r.status_code, len(r.content), 'set-cookie' in headers_lower)
        except (requests.exceptions.RequestException, Exception):
            result = None
        with probe_lock:
            probe_cache[url] = result
        return result

    def _rtsp_task(port, username, password):
        if found_event.is_set() or _time_left() <= 0 or not threads_running:
            return None
        _bump("RTSP", port)
        if test_rtsp_credentials(ip, port, username, password):
            return ("rtsp", port, None, username, password)
        return None

    def _http_task(port, path, auth_type, username, password):
        """Test one HTTP credential against a URL.

        Guards against the "any 200 is success" false positive: we first probe
        the URL without credentials. For basic-auth endpoints we insist the
        unauth response be 401/403 (otherwise no auth is enforced and creds
        mean nothing). For form-auth we look at whether the credentialed POST
        response *differs* meaningfully from the unauth GET — a session cookie
        appearing or a redirect landing indicate a real login, whereas a
        matching 200 just means the login form re-rendered.
        """
        if found_event.is_set() or _time_left() <= 0 or not threads_running:
            return None
        protocol = get_protocol(port, ip)
        url = f"{protocol}://{ip}:{port}{path}"

        probe = _probe_unauth(url)
        if probe is None:
            return None
        unauth_status, _unauth_len, unauth_setcookie = probe

        if auth_type == "basic":
            if unauth_status not in (401, 403):
                # No auth is enforced here — testing creds is pointless.
                with probe_lock:
                    skipped_urls.add(url)
                return None
        # For form auth we still try, but validation below is stricter than
        # "any 200 counts."

        _bump("HTTP", port)
        try:
            if auth_type == "basic":
                response = SESSION.get(url, auth=(username, password),
                                       timeout=CREDENTIAL_TIMEOUT, verify=False,
                                       allow_redirects=False)
                # Success = unauth was gated (asserted above) and auth got 200.
                if response.status_code == 200:
                    return ("http", port, url, username, password)
            elif auth_type == "form":
                response = SESSION.post(url,
                                        data={'username': username, 'password': password},
                                        timeout=CREDENTIAL_TIMEOUT, verify=False,
                                        allow_redirects=False)
                headers_lower = {k.lower() for k in response.headers.keys()}
                new_setcookie = 'set-cookie' in headers_lower
                # Real form login: server issues a session cookie, or redirects
                # away from the login page. A bare 200 (login form re-rendered)
                # is not sufficient — that's how we got false positives before.
                if response.status_code in (301, 302, 303, 307, 308):
                    return ("http", port, url, username, password)
                if new_setcookie and not unauth_setcookie:
                    return ("http", port, url, username, password)
        except (requests.exceptions.RequestException, Exception):
            pass
        return None

    def _run_batch(tasks):
        """Submit `tasks` to a pool; drain as_completed until success or budget out."""
        if not tasks:
            return
        with ThreadPoolExecutor(max_workers=30) as pool:
            futures = [pool.submit(fn, *args) for fn, args in tasks]
            for future in as_completed(futures):
                if found_event.is_set() or _time_left() <= 0 or not threads_running:
                    break
                result = future.result()
                if result:
                    successes.append(result)
                    found_event.set()
                    break

    # PRIORITY 1: RTSP + web with the short PRIORITY_CREDENTIALS list.
    priority_tasks = []
    for port in all_rtsp_ports:
        for u, p in PRIORITY_CREDENTIALS:
            priority_tasks.append((_rtsp_task, (port, u, p)))
    endpoints = [("/", "basic"), ("/login", "form")]
    for port in web_ports:
        for path, auth in endpoints:
            for u, p in PRIORITY_CREDENTIALS:
                priority_tasks.append((_http_task, (port, path, auth, u, p)))
    _run_batch(priority_tasks)

    # PRIORITY 2: remaining creds, tighter port set, only if we have >30% budget left.
    if not found_event.is_set() and _time_left() > MAX_CREDENTIAL_TEST_TIME * 0.3:
        priority_set = set(PRIORITY_CREDENTIALS)
        extra_credentials = [
            (u, p)
            for u, ps in DEFAULT_CREDENTIALS.items()
            for p in ps
            if (u, p) not in priority_set
        ][:30]
        if len(extra_credentials) == 30:
            total_extra = sum(len(ps) for ps in DEFAULT_CREDENTIALS.values()) - len(priority_set)
            if total_extra > 30:
                print(f"{Y}[ℹ️] {total_extra - 30} extra credential(s) dropped "
                      f"(round-2 cap 30).{W}")

        follow_tasks = []
        for port in all_rtsp_ports[:3]:
            for u, p in extra_credentials:
                follow_tasks.append((_rtsp_task, (port, u, p)))
        for port in web_ports[:3]:
            for u, p in extra_credentials:
                follow_tasks.append((_http_task, (port, "/", "basic", u, p)))
        _run_batch(follow_tasks)

    elapsed = int(time.time() - start_time)
    if _time_left() <= 0:
        print(f"{Y}[⚠️] Credential testing stopped after {MAX_CREDENTIAL_TEST_TIME}s timeout{W}")
    else:
        print(f"{C}[✓] Tested {tested_count[0]} credentials in {elapsed}s{W}")

    if skipped_urls:
        print(f"{C}[ℹ️] Skipped credential testing on {len(skipped_urls)} URL(s) that "
              f"returned 200 unauthenticated — no auth is enforced there:{W}")
        for u in sorted(skipped_urls):
            print(f"     · {u}")

    if successes:
        for kind, port, url, user, pw in successes:
            if kind == "rtsp":
                print(f"🔥 Success! RTSP {user}:{pw} @ rtsp://{ip}:{port}/")
            else:
                print(f"🔥 Success! {user}:{pw} @ {url}")
    else:
        print("❌ No default credentials found")

def try_default_credentials(ip, port):
    """Attempt to find working credentials for fingerprinting.

    Requires the operator to have asserted authorisation (--i-have-authorisation).
    Without that flag, returns None immediately — no unauthorised login attempts.
    """
    if not CONFIG.authorised or CONFIG.no_brute:
        return None
    protocol = get_protocol(port, ip)
    base = f"{protocol}://{ip}:{port}/"
    for username, passwords in DEFAULT_CREDENTIALS.items():
        for password in passwords:
            try:
                response, _ = _capped_get(base, cap=1, auth=(username, password))
                if response.status_code == 200:
                    return f"{username}:{password}"
            except (requests.exceptions.RequestException, Exception):
                pass
    return None

def search_cve(brand):
    """Enhanced CVE lookup functionality"""
    print(f"\n[🛡️] Checking known CVEs for {brand.capitalize()}:")
    if cves := CVE_DATABASE.get(brand.lower()):
        for cve in cves:
            print(f"  🔗 https://nvd.nist.gov/vuln/detail/{cve}")
    else:
        print("  ℹ️ No common CVEs found for this brand")

def fingerprint_camera(ip, open_ports):
    print(f"\n[📡] {C}Scanning for Camera Type & Firmware:{W}")
    for port in open_ports:
        protocol = get_protocol(port, ip)
        url_base = f"{protocol}://{ip}:{port}"
        print(f"🔍 Checking {url_base}...")
        try:
            resp, body = _capped_get(url_base)
            server_header = resp.headers.get("server", "").lower()
            content = body.lower()
            
            if "hikvision" in server_header:
                print("🔥 Hikvision Camera Detected!")
                fingerprint_hikvision(ip, port)
            elif "dahua" in server_header:
                print("🔥 Dahua Camera Detected!")
                fingerprint_dahua(ip, port)
            elif "axis" in server_header:
                print("🔥 Axis Camera Detected!")
                fingerprint_axis(ip, port)
            elif any(x in content for x in ['cp plus', 'cp-plus', 'cpplus', 'cp_plus', 'uvr', '0401e1']):
                print("🔥 CP Plus Camera Detected!")
                fingerprint_cp_plus(ip, port)
            else:
                print("❓ Unknown Camera Type")
                fingerprint_generic(ip, port)
        except (requests.exceptions.RequestException, Exception):
            print("❌ No response")

def fingerprint_hikvision(ip, port):
    print("➡️  Attempting Hikvision Fingerprint...")
    protocol = get_protocol(port, ip)

    # Hikvision uses HTTP Basic/Digest headers, not a `?auth=` query param;
    # the old query-string variant that used to be here was never real.
    auth = None
    if CONFIG.authorised and not CONFIG.no_brute:
        creds = try_default_credentials(ip, port)
        if creds:
            u, _, p = creds.partition(":")
            auth = (u, p)

    endpoints = [
        f"{protocol}://{ip}:{port}/ISAPI/System/deviceInfo",
        f"{protocol}://{ip}:{port}/System/deviceInfo",
    ]

    for url in endpoints:
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT,
                                verify=False, auth=auth, stream=True)
            if resp.status_code == 401:
                print(f"⚠️ Authentication required for {url}")
                continue
            if resp.status_code == 200:
                # Cap body — hostile server could hand us gigabytes of "XML".
                body_bytes = bytearray()
                for chunk in resp.iter_content(chunk_size=8192):
                    body_bytes.extend(chunk)
                    if len(body_bytes) >= MAX_XML_BYTES:
                        break
                print(f"✅ Found at {url}")
                try:
                    xml_root = ET.fromstring(bytes(body_bytes))
                    model = xml_root.findtext(".//{*}deviceName") or xml_root.findtext(".//model")
                    firmware = xml_root.findtext(".//{*}firmwareVersion") or xml_root.findtext(".//firmwareVersion")
                    if model:
                        print(f"📸 Model: {model}")
                    if firmware:
                        print(f"🛡️ Firmware: {firmware}")
                    if not _XML_HARDENED:
                        print("    ℹ️  (Install `defusedxml` for hardened XML parsing.)")
                except ET.ParseError:
                    print("⚠️ Cannot parse XML configuration")
        except Exception as e:
            print(f"⚠️ {e}")
    search_cve("hikvision")

def fingerprint_dahua(ip, port):
    print("➡️  Attempting Dahua Fingerprint...")
    protocol = get_protocol(port, ip)
    try:
        url = f"{protocol}://{ip}:{port}/cgi-bin/magicBox.cgi?action=getSystemInfo"
        resp, body = _capped_get(url)
        if resp.status_code == 200:
            print(f"✅ Found at {url}")
            print(body.strip())
        else:
            print(f"❌ {url} -> HTTP {resp.status_code}")
    except Exception as e:
        print(f"⚠️ {e}")
    search_cve("dahua")

def fingerprint_axis(ip, port):
    print("➡️  Attempting Axis Fingerprint...")
    protocol = get_protocol(port, ip)
    try:
        url = f"{protocol}://{ip}:{port}/axis-cgi/admin/param.cgi?action=list"
        resp, body = _capped_get(url)
        if resp.status_code == 200:
            print(f"✅ Found at {url}")
            for line in body.splitlines():
                if any(x in line for x in ["root.Brand", "root.Model", "root.Firmware"]):
                    print(f"🔹 {line.strip()}")
        else:
            print(f"❌ {url} -> HTTP {resp.status_code}")
    except Exception as e:
        print(f"⚠️ {e}")
    search_cve("axis")

def fingerprint_cp_plus(ip, port):
    print("➡️  Attempting CP Plus Fingerprint...")
    protocol = get_protocol(port, ip)
    
    # CP Plus specific endpoints
    endpoints = [
        f"{protocol}://{ip}:{port}/",
        f"{protocol}://{ip}:{port}/index.html",
        f"{protocol}://{ip}:{port}/login",
        f"{protocol}://{ip}:{port}/admin",
        f"{protocol}://{ip}:{port}/cgi-bin",
        f"{protocol}://{ip}:{port}/api",
        f"{protocol}://{ip}:{port}/config"
    ]
    
    for url in endpoints:
        try:
            resp, body = _capped_get(url)
            if resp.status_code == 200:
                print(f"✅ Found at {url}")
                content = body.lower()
                
                # Look for CP Plus specific information
                if 'uvr-0401e1' in content or 'uvr0401e1' in content:
                    print(f"📸 Model: CP-UVR-0401E1-IC2")
                if 'cp plus' in content or 'cpplus' in content:
                    print(f"🏢 Brand: CP Plus")
                if 'dvr' in content:
                    print(f"📺 Device Type: DVR")
                
                # Print first 500 characters for analysis
                print(f"📄 Response Preview: {body[:500]}")
                break
        except Exception as e:
            print(f"⚠️ {e}")
    
    search_cve("cp plus")

def fingerprint_generic(ip, port):
    print("➡️  Attempting Generic Fingerprint...")
    protocol = get_protocol(port, ip)
    endpoints = [
        "/System/configurationFile",
        "/ISAPI/System/deviceInfo",
        "/cgi-bin/magicBox.cgi?action=getSystemInfo",
        "/axis-cgi/admin/param.cgi?action=list",
        "/",
        "/index.html",
        "/login",
        "/admin",
        "/cgi-bin",
        "/api",
        "/config"
    ]
    brand_keywords = {
        "hikvision": ["hikvision"],
        "dahua": ["dahua"],
        "axis": ["axis"],
        "cp plus": ["cp plus", "cp-plus", "cpplus", "cp_plus", "uvr", "0401e1"],
    }
    detected_brand = None
    for path in endpoints:
        url = f"{protocol}://{ip}:{port}{path}"
        try:
            resp, body = _capped_get(url)
            if resp.status_code == 200:
                print(f"✅ Found at {url}")
                snippet = body[:500]
                print(snippet)
                # Try to detect brand in response text or headers
                text = (body + " " + str(resp.headers)).lower()
                for brand, keywords in brand_keywords.items():
                    if any(keyword in text for keyword in keywords):
                        detected_brand = brand
                        break
                if detected_brand:
                    search_cve(detected_brand)
                    break  # Continue checking other endpoints
        except (requests.exceptions.RequestException, Exception):
            pass
    if not detected_brand:
        print("❌ No common endpoints responded.")

def detect_camera_brand(ip, open_ports):
    """Detect camera brand from HTTP responses"""
    detected_brands = set()
    
    # Brand indicators in URLs, content, or headers
    brand_indicators = {
        'axis': ['/view/index.shtml', '/axis-cgi/', 'axis', 'axis communications'],
        'hikvision': ['hikvision', '/ISAPI/', '/Streaming/'],
        'dahua': ['dahua', '/cgi-bin/magicBox.cgi'],
        'sony': ['sony', 'ipela'],
        'panasonic': ['panasonic', 'network camera'],
    }
    
    for port in open_ports[:5]:  # Check first 5 ports
        try:
            protocol = get_protocol(port, ip)
            url = f"{protocol}://{ip}:{port}/"
            response, body = _capped_get(url, timeout=2)

            if response.status_code == 200:
                content = body.lower()
                url_lower = url.lower()
                
                # Check for brand indicators
                for brand, indicators in brand_indicators.items():
                    if any(ind in content or ind in url_lower for ind in indicators):
                        detected_brands.add(brand)
        except (requests.exceptions.RequestException, Exception):
            continue
    
    return detected_brands

def detect_live_streams(ip, open_ports, rtsp_ports=None):
    """Enhanced live stream detection with better methods"""
    print(f"\n{C}[🎥] Checking for Live Streams:{W}")
    found_streams = False
    
    if rtsp_ports is None:
        rtsp_ports = []
    
    # Detect camera brands that might support RTSP
    detected_brands = detect_camera_brand(ip, open_ports)
    
    # Common streaming protocols and their default ports
    streaming_ports = {
        'rtsp': [554, 8554, 10554],  # Multiple RTSP ports
        'rtmp': [1935, 1936],
        'http': [80, 8080, 8000, 8001],
        'https': [443, 8443, 8444],
        'mms': [1755],
        'onvif': [3702, 80, 443],  # ONVIF discovery and streaming
        'vlc': [8080, 8090]  # VLC streaming ports
    }
    
    # FIRST: Show RTSP links for RTSP ports (detected + standard RTSP ports that are open)
    # Combine detected RTSP ports with standard RTSP ports that are open
    all_rtsp_ports = set(rtsp_ports) | set([p for p in open_ports if p in streaming_ports['rtsp']])
    
    # ALSO: For known camera brands that support RTSP, suggest RTSP URLs even if not detected
    # Brands that commonly support RTSP: Axis, Hikvision, Dahua, Sony, Panasonic
    rtsp_supporting_brands = {'axis', 'hikvision', 'dahua', 'sony', 'panasonic'}
    if detected_brands & rtsp_supporting_brands and not all_rtsp_ports:
        # Camera brand detected but no RTSP ports found - suggest RTSP on common ports
        suggested_rtsp_ports = [554]  # Standard RTSP port
        # Also suggest RTSP on HTTP ports if it's a known brand
        for port in open_ports:
            if port in [80, 443, 8000, 8080] and port not in all_rtsp_ports:
                suggested_rtsp_ports.append(port)
        
        all_rtsp_ports = all_rtsp_ports | set(suggested_rtsp_ports)
        if suggested_rtsp_ports:
            brand_names = ', '.join([b.capitalize() for b in detected_brands & rtsp_supporting_brands])
            print(f"\n{C}[🎯] {brand_names} Camera Detected - Suggesting RTSP URLs (RTSP may be available):{W}")
    
    if all_rtsp_ports:
        found_streams = True  # Mark streams as found if RTSP ports detected
        # Only show "RTSP Ports Found" header if not already shown for brand detection
        if not (detected_brands & rtsp_supporting_brands and not rtsp_ports):
            print(f"\n{C}[🎯] RTSP Ports Found - Potential RTSP URLs:{W}")
        rtsp_ports_sorted = sorted(all_rtsp_ports)
        for port in rtsp_ports_sorted:
            # Show common RTSP paths
            common_paths = ['/', '/live.sdp', '/h264.sdp', '/stream1', '/Streaming/Channels/1']
            # Brand-specific paths
            if 'axis' in detected_brands:
                common_paths.extend(['/axis-media/media.amp', '/axis-media/media.amp?camera=1'])
            if 'hikvision' in detected_brands:
                common_paths.extend(['/Streaming/Channels/101', '/Streaming/Channels/1'])
            
            for path in common_paths[:5]:  # Limit to 5 paths per port
                rtsp_url = f"rtsp://{ip}:{port}{path}"
                print(f"  🎥 RTSP: {rtsp_url}")
            print(f"     🎯 Use VLC (Media -> Open Network Stream) to test these RTSP URLs")
        print()  # Empty line for readability
    
    # Stream paths per protocol. Deduplicated (the previous HTTP list had
    # five duplicate entries: /snapshot.jpg, /img/snapshot.cgi,
    # /cgi-bin/snapshot.cgi, /cgi-bin/viewer/video.jpg, /mjpg/video.mjpg).
    # dict.fromkeys(...) preserves insertion order while removing repeats.
    stream_paths = {
        'rtsp': list(dict.fromkeys([
            # Generic
            '/live.sdp', '/h264.sdp', '/stream1', '/stream2', '/main', '/sub',
            '/video', '/cam/realmonitor',
            '/Streaming/Channels/1', '/Streaming/Channels/101',
            # Brand / vendor
            '/onvif/streaming/channels/1',   # ONVIF
            '/axis-media/media.amp',         # Axis
            '/axis-cgi/mjpg/video.cgi',      # Axis
            '/cgi-bin/mjpg/video.cgi',
            '/cgi-bin/hi3510/snap.cgi',      # Hikvision-style
            '/cgi-bin/snapshot.cgi',
            '/cgi-bin/viewer/video.jpg',
            '/img/snapshot.cgi',
            '/snapshot.jpg',
            '/video/mjpg.cgi', '/video.cgi', '/videostream.cgi',
            '/mjpg/video.mjpg', '/mjpg.cgi',
            '/stream.cgi', '/live.cgi',
            # ONVIF-style SDP endpoints
            '/live/0/onvif.sdp', '/live/0/h264.sdp',
            '/live/0/mpeg4.sdp', '/live/0/audio.sdp',
            '/live/1/onvif.sdp', '/live/1/h264.sdp',
            '/live/1/mpeg4.sdp', '/live/1/audio.sdp',
        ])),
        'rtmp': list(dict.fromkeys([
            '/live', '/stream', '/hls', '/flv', '/rtmp',
            '/live/stream', '/live/stream1', '/live/stream2',
            '/live/main', '/live/sub', '/live/video', '/live/audio',
            '/live/av', '/live/rtmp', '/live/rtmps',
        ])),
        'http': list(dict.fromkeys([
            # Snapshot / MJPEG endpoints
            '/video', '/stream',
            '/mjpg/video.mjpg', '/mjpg.cgi',
            '/cgi-bin/mjpg/video.cgi',
            '/axis-cgi/mjpg/video.cgi',
            '/cgi-bin/viewer/video.jpg',
            '/snapshot.jpg', '/img/snapshot.cgi',
            # ONVIF
            '/onvif/device_service', '/onvif/streaming',
            # Axis control
            '/axis-cgi/com/ptz.cgi', '/axis-cgi/param.cgi',
            # Generic CGI (CP Plus + others)
            '/cgi-bin/snapshot.cgi', '/cgi-bin/hi3510/snap.cgi',
            '/cgi-bin/video.cgi', '/cgi-bin/stream.cgi', '/cgi-bin/live.cgi',
            '/video/mjpg.cgi', '/video.cgi', '/videostream.cgi',
            '/stream.cgi', '/live.cgi',
            # REST-ish API endpoints
            '/api/video', '/api/stream', '/api/live',
            '/api/video/live', '/api/stream/live',
            '/api/camera/live', '/api/camera/stream', '/api/camera/video',
            '/api/camera/snapshot', '/api/camera/image', '/api/camera/feed',
            '/api/camera/feed/live', '/api/camera/feed/stream', '/api/camera/feed/video',
        ])),
    }
    
    def _tcp_reachable(host, port, timeout=2.0):
        """Cheap TCP-connect probe used by non-HTTP scheme checks."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    def _check_http_stream(url):
        """HTTP/HTTPS stream check using requests. Body is capped and closed."""
        lower = url.lower()
        try:
            # verify=False: cameras almost always ship self-signed certs
            with SESSION.get(url, timeout=TIMEOUT, verify=False,
                              stream=True, headers=HEADERS) as response:
                if response.status_code != 200:
                    return False
                content_type = response.headers.get('Content-Type', '').lower()
                content_length = response.headers.get('Content-Length', '0')
                # Drain a small sniff of the body so the socket returns to the
                # pool; we deliberately never read the whole thing.
                try:
                    next(response.iter_content(chunk_size=1024), None)
                except Exception:
                    pass

            is_stream_content = any(x in content_type for x in (
                'video', 'stream', 'mpeg', 'h264', 'mjpeg',
                'multipart/x-mixed-replace',
            ))
            is_snapshot = content_type.startswith('image/') and any(
                p in lower for p in ('snapshot', 'snap.cgi', 'jpg', 'mjpg', 'video.cgi')
            )
            path_hints = any(p in lower for p in ('/video', '/stream', '/live', '/mjpg', '/snapshot'))
            file_ext = any(x in lower for x in ('.mp4', '.m3u8', '.ts', '.flv', '.webm', '.avi', '.mov'))

            if is_stream_content or is_snapshot or file_ext or path_hints:
                label = 'Stream Found' if is_stream_content else (
                    'Snapshot Found' if is_snapshot else (
                        'Video File' if file_ext else 'Potential Stream'
                    )
                )
                print(f"  ✅ {label}: {url}")
                print(f"     📺 Content-Type: {content_type}")
                if content_length and content_length != '0':
                    print(f"     📏 Content-Length: {content_length}")
                print(f"     🌐 HTTP/HTTPS - Open in browser: {url}")
                return True
        except requests.exceptions.RequestException:
            pass
        return False

    def _check_rtsp_stream(url):
        """RTSP stream check.

        Only reports a per-path hit when DESCRIBE returns 200. A 401 tells us
        only that an RTSP server exists on this port — which is true for every
        path, so reporting it per URL just spams the operator with URLs that
        are unlikely to exist (the "RTSP Ports Found" preamble already tells
        them to use VLC with a suggested URL and their creds).
        """
        m = re.match(r"rtsp://([^:/]+):(\d+)(/.*)?$", url)
        if not m:
            return False
        host, port_s, path = m.group(1), m.group(2), (m.group(3) or "/")
        try:
            resp = _rtsp_request(host, int(port_s), "DESCRIBE", path=path)
        except Exception:
            return False
        if _rtsp_status(resp) == 200:
            print(f"  ✅ RTSP Stream Found (unauthenticated): {url}")
            print(f"     🎯 Use VLC (Media -> Open Network Stream): {url}")
            return True
        return False

    def _check_socket_stream(url, scheme):
        """RTMP/MMS/etc — we can only confirm the TCP port is reachable."""
        m = re.match(rf"{scheme}://([^:/]+):(\d+)", url)
        if not m:
            return False
        host, port_s = m.group(1), m.group(2)
        if _tcp_reachable(host, int(port_s)):
            print(f"  ✅ {scheme.upper()} port reachable: {url}")
            print(f"     🎯 Try in VLC (Media -> Open Network Stream): {url}")
            return True
        return False

    def check_stream_with_details(url):
        """Dispatch stream checking based on URL scheme."""
        lower = url.lower()
        if lower.startswith(('http://', 'https://')):
            return _check_http_stream(url)
        if lower.startswith('rtsp://'):
            return _check_rtsp_stream(url)
        if lower.startswith('rtmp://'):
            return _check_socket_stream(url, 'rtmp')
        if lower.startswith('mms://'):
            return _check_socket_stream(url, 'mms')
        return False
    
    # Build the full URL work-list first, then hand it to a pool. This
    # replaces five separate batch-of-30 thread loops (each of which
    # blocked on `.join()` before starting the next batch) with a single
    # ThreadPoolExecutor that keeps 30 slots hot for the whole scan.
    rtsp_ports_to_check = set(rtsp_ports) | set(streaming_ports['rtsp'])
    urls_to_check = []

    for port in open_ports:
        if port in rtsp_ports_to_check:
            urls_to_check.extend(f"rtsp://{ip}:{port}{path}" for path in stream_paths['rtsp'])
        if port in streaming_ports['rtmp']:
            urls_to_check.extend(f"rtmp://{ip}:{port}{path}" for path in stream_paths['rtmp'])
        if port in streaming_ports['http'] + streaming_ports['https']:
            protocol = 'https' if port in streaming_ports['https'] else 'http'
            urls_to_check.extend(f"{protocol}://{ip}:{port}{path}" for path in stream_paths['http'])
        if port in streaming_ports['mms']:
            urls_to_check.append(f"mms://{ip}:{port}")
        if port in streaming_ports['onvif']:
            urls_to_check.append(f"http://{ip}:{port}/onvif/device_service")

    if urls_to_check:
        with ThreadPoolExecutor(max_workers=30) as pool:
            futures = [pool.submit(check_stream_with_details, url) for url in urls_to_check]
            for future in as_completed(futures):
                if not threads_running:
                    break
                # `check_stream_with_details` already prints on success; here we
                # only need to track whether ANY confirmed a stream (fixes the
                # old bug of marking found_streams=True just because a check
                # was started, regardless of the result).
                try:
                    if future.result():
                        found_streams = True
                except Exception:
                    pass
    
    if not found_streams:
        print("  ❌ No live streams detected")
    else:
        print(f"  📊 Stream detection completed")
        if all_rtsp_ports:
            print(f"\n{C}[ℹ️] RTSP Streams Detected - To view RTSP/RTMP streams in VLC:{W}")
            print("    1. Open VLC Media Player")
            print("    2. Go to 'Media' -> 'Open Network Stream'")
            print("    3. Paste the RTSP URL (e.g., rtsp://IP:PORT/) and click 'Play'")
        
        # Always show HTTP/HTTPS message if streams were found
        print(f"\n{C}[ℹ️] HTTP/HTTPS streams can be opened directly in your web browser{W}")
        print(f"     💡 Tip: Look above for HTTP/HTTPS stream URLs (e.g., http://IP:PORT/mjpg/video.mjpg)")

def _build_argparser():
    p = argparse.ArgumentParser(
        prog="CamXploit",
        description=(
            "IP-camera / CCTV reconnaissance scanner. "
            "Only run against systems you own or have explicit written "
            "authorisation to test."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Active probing (default-credential brute-force against RTSP or "
            "HTTP) requires the --i-have-authorisation flag. Without it, the "
            "scanner performs recon only."
        ),
    )
    p.add_argument("target", nargs="?",
                   help="Target as IPv4 or IPv4:PORT (falls back to prompt if omitted).")
    p.add_argument("--i-have-authorisation", action="store_true", dest="authorised",
                   help="Assert you have written authorisation to actively probe "
                        "the target. Required to enable credential brute-force.")
    p.add_argument("--no-brute", action="store_true",
                   help="Skip credential testing even if --i-have-authorisation is set.")
    p.add_argument("--no-osint", action="store_true",
                   help="Skip third-party lookups (ipinfo.io, Shodan/Censys/Zoomeye link output).")
    p.add_argument("--threads", type=int, default=100,
                   help="Concurrency for the port scan (default: 100).")
    p.add_argument("--yes", action="store_true",
                   help="Answer 'yes' to interactive prompts (non-interactive runs).")
    return p


def main(argv=None):
    global threads_running
    parser = _build_argparser()
    args = parser.parse_args(argv)

    CONFIG.authorised = args.authorised
    CONFIG.no_brute   = args.no_brute or not args.authorised
    CONFIG.no_osint   = args.no_osint
    CONFIG.threads    = max(1, min(args.threads, 500))

    try:
        if args.target:
            user_input = args.target.strip()
        else:
            try:
                user_input = input(f"{G}[+] {C}Enter IP address (or IP:PORT): {W}").strip()
            except EOFError:
                print(f"\n{R}[!] No target provided and no interactive input available. "
                      f"Pass the target on the command line (e.g. CamXploit.py 1.2.3.4).{W}")
                return 2

        target_ip, specified_port = parse_ip_port(user_input)
        if target_ip is None:
            return 2

        if not validate_ip(target_ip):
            return 2

        ip_obj = ipaddress.ip_address(target_ip)

        if specified_port is not None:
            print(f"{C}[ℹ️] Port {specified_port} specified - will prioritize scanning this port{W}")

        print(BANNER)
        print('____________________________________________________________________________\n')

        # Advertise the current authorisation posture up-front so an operator
        # who forgot the flag doesn't wait for a scan then wonder why no
        # brute-force ran.
        if CONFIG.authorised and not CONFIG.no_brute:
            print(f"{Y}[⚠️] Active probing ENABLED — you asserted authorisation for {target_ip}.{W}")
        elif CONFIG.authorised and CONFIG.no_brute:
            print(f"{C}[ℹ️] Authorised, but --no-brute is set. Recon only.{W}")
        else:
            print(f"{C}[ℹ️] Recon-only mode. Add --i-have-authorisation to enable credential testing.{W}")

        # OSINT: skip for private / loopback / link-local / multicast, or when
        # the operator disabled it.
        skip_osint = (
            CONFIG.no_osint
            or ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
        )
        if skip_osint:
            print(f"{Y}[🏠] OSINT lookups skipped (special-use IP or --no-osint).{W}")
        else:
            print_search_urls(target_ip)
            google_dork_search(target_ip)
            get_ip_location_info(target_ip)

        additional = [specified_port] if specified_port is not None else None
        open_ports, rtsp_ports = check_ports(target_ip, additional_ports=additional)

        if not open_ports:
            print("\n[❌] No open ports found. Likely no camera here.")
            print("\n[✅] Scan Completed!")
            return 0

        camera_found = check_if_camera(target_ip, open_ports, rtsp_ports)

        if not camera_found and not skip_osint and not args.yes:
            try:
                choice = input("\n[❓] No camera found. Continue checking login pages, "
                               "fingerprints, and passwords? [y/N]: ").strip().lower()
            except EOFError:
                choice = "n"
            if choice != "y":
                print("\n[✅] Scan Completed! No camera found.")
                return 0

        check_login_pages(target_ip, open_ports)
        fingerprint_camera(target_ip, open_ports)
        if not CONFIG.no_brute:
            test_default_passwords(target_ip, open_ports, rtsp_ports)
        else:
            print(f"\n{C}[🔒] Skipping credential testing (authorisation not asserted or --no-brute).{W}")
        detect_live_streams(target_ip, open_ports, rtsp_ports)

        print("\n[✅] Scan Completed!")
        return 0

    except KeyboardInterrupt:
        print("\n[!] Scan aborted by user")
        threads_running = False
        return 130


if __name__ == "__main__":
    sys.exit(main() or 0)
