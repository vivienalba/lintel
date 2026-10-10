"""Bounded read-only HTTP client. Resolve and pin public IPs for every hop.

The browser adapter also uses this transport. No private-network escape flag.
Tests use a fake transport; live scans always enforce public addresses.
"""
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit, urljoin, urldefrag, quote
from urllib.robotparser import RobotFileParser
import http.client
import ipaddress
import socket
import ssl
import certifi
import time


def tls_context():
    """Keep system trust and supplement it for Python installations without bundled roots."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


class ScanError(Exception):
    pass


def normalize(url, base=None):
    url = urljoin(base, url.strip()) if base else url.strip()
    if not base and "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        raise ScanError("Use a public HTTP or HTTPS URL without embedded credentials.")
    if any(c.isspace() for c in p.hostname):
        raise ScanError("The URL does not appear to be valid. Enter a website address such as https://example.com.")
    try:
        port = p.port
    except ValueError as exc:
        raise ScanError("Invalid port.") from exc
    if port not in (None, 80, 443):
        raise ScanError("Only standard web ports 80 and 443 are supported.")
    host = p.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = "[" + host + "]"
    default = 443 if p.scheme == "https" else 80
    netloc = host + (f":{port}" if port and port != default else "")
    path = quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(p.query, safe="=&/%:@!$'()*+,;?-._~")
    return urlunsplit((p.scheme, netloc, path, query, ""))


def origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def public_addresses(host, port):
    addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    if not addresses:
        raise ScanError("The hostname did not resolve.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or (mapped is not None and not mapped.is_global):
            raise ScanError("Private, local, reserved, and metadata network addresses are blocked.")
    return addresses


@dataclass
class Response:
    url: str
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    error: str = ""
    chain: list = field(default_factory=list)
    elapsed: float = 0
    truncated: bool = False

    @property
    def text(self):
        content = self.headers.get("content-type", "")
        encoding = content.split("charset=")[-1].split(";")[0].strip() if "charset=" in content else "utf-8"
        try:
            return self.body.decode(encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


class SafeClient:
    def __init__(self, max_requests=150, max_seconds=180, max_bytes=5_000_000):
        self.max_requests = max_requests
        self.deadline = time.monotonic() + max_seconds
        self.max_bytes = max_bytes
        self.count = 0
        self.total_bytes = 0
        self.cache = {}
        self.robots = {}
        self.robots_errors = {}
        self.last_request = 0

    def _one(self, url):
        if self.count >= self.max_requests or time.monotonic() >= self.deadline or self.total_bytes >= 50_000_000:
            raise ScanError("Scan request or time limit reached; remaining URLs were not checked.")
        wait = .08 - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        addr = public_addresses(p.hostname, port)[0]
        timeout = max(.1, min(8, self.deadline-time.monotonic()))
        connection = (http.client.HTTPSConnection(p.hostname, port, timeout=timeout, context=tls_context())
                      if p.scheme == "https" else http.client.HTTPConnection(p.hostname, port, timeout=timeout))
        # Hostname and TLS SNI remain original. Only the TCP destination is pinned.
        connection._create_connection = lambda address, timeout=None, source_address=None: socket.create_connection((addr, port), timeout, source_address)
        self.count += 1
        self.last_request = time.monotonic()
        started = time.monotonic()
        try:
            connection.request("GET", p.path + ("?"+p.query if p.query else ""), headers={"User-Agent": "WebsiteQualityChecker/1.0", "Accept-Encoding": "identity", "Accept": "*/*"})
            result = connection.getresponse()
            headers = {k.lower(): v for k, v in result.getheaders()}
            body = bytearray()
            while len(body) <= self.max_bytes:
                if time.monotonic() >= self.deadline:
                    raise ScanError("Scan time limit reached while reading a response.")
                chunk = result.read1(min(65536, self.max_bytes+1-len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                self.total_bytes += len(chunk)
                if self.total_bytes >= 50_000_000:
                    raise ScanError("Scan download limit reached (50 MB).")
            truncated = len(body) > self.max_bytes
            return Response(url, result.status, headers, bytes(body[:self.max_bytes]), elapsed=round(time.monotonic()-started, 3), truncated=truncated)
        finally:
            connection.close()

    def allowed(self, url):
        root = origin(url)
        if root not in self.robots:
            result = self.fetch(root + "/robots.txt", robots=False)
            parser = RobotFileParser()
            if result.error or result.status >= 500 or result.status in (401,403,429):
                self.robots[root] = None
                self.robots_errors[root] = "Could not verify robots.txt at " + root + "/robots.txt: " + (result.error or f"HTTP {result.status}")
            elif 200 <= result.status < 300:
                parser.parse(result.text.splitlines())
                self.robots[root] = parser
            elif result.status in (404,410):
                parser.parse([])
                self.robots[root] = parser
            else:
                self.robots[root] = None
                self.robots_errors[root] = f"Could not verify robots.txt at {root}/robots.txt: HTTP {result.status}"
        parser = self.robots[root]
        return parser is not None and parser.can_fetch("WebsiteQualityChecker", url)

    def fetch(self, url, robots=True, follow=True):
        try:
            url = normalize(url)
            key = (url, robots, follow)
            if key in self.cache:
                return self.cache[key]
            current, chain, visited = url, [], set()
            for _ in range(7):
                if current in visited:
                    raise ScanError("Redirect loop detected.")
                visited.add(current)
                if robots and not self.allowed(current):
                    raise ScanError(self.robots_errors.get(origin(current),"The website’s robots.txt disallows WebsiteQualityChecker from checking this URL."))
                result = self._one(current)
                if result.status in (301,302,303,307,308) and result.headers.get("location") and follow:
                    chain.append({"url": current, "status": result.status})
                    current = normalize(result.headers["location"], current)
                    continue
                result.chain = chain
                self.cache[key] = result
                return result
            raise ScanError("Redirect chain exceeded six hops.")
        except (ScanError, OSError, ValueError, http.client.HTTPException, UnicodeError) as exc:
            return Response(url, error=str(exc))
