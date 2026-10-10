"""Bounded public-Internet HTTP client for administrator-configured addons.
DNS is resolved and validated before connecting directly to the selected address.
Redirects undergo the same checks; upstream URLs are never included in errors.
"""
import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

MAX_JSON_BYTES = 4 * 1024 * 1024

class AddonHTTPError(Exception):
    pass

def public_address(value):
    address = ipaddress.ip_address(value)
    if getattr(address, "ipv4_mapped", None):
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_reserved

def parse_public_url(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 32 for c in url):
        raise AddonHTTPError("Invalid URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise AddonHTTPError("Invalid URL") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise AddonHTTPError("Only public HTTP(S) URLs are supported")
    if port not in (80, 443):
        raise AddonHTTPError("Only standard web ports are supported")
    return parsed, port

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=15, context=ssl.create_default_context())
        self.address = address
    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=15)
        self.address = address
    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)

def open_public(url, headers=None, method="GET"):
    for _ in range(4):
        parsed, port = parse_public_url(url)
        try:
            records = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(r[4][0] for r in records))
            if not addresses or not all(public_address(a) for a in addresses):
                raise AddonHTTPError("Private destinations are blocked")
            cls = PinnedHTTPS if parsed.scheme == "https" else PinnedHTTP
            connection = cls(parsed.hostname, port, addresses[0])
            safe_headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
            for key, value in (headers or {}).items():
                if key.lower() in ("range", "if-range", "user-agent", "referer", "origin", "authorization"):
                    if isinstance(value, str) and "\r" not in value and "\n" not in value:
                        safe_headers[key] = value
            connection.request(method, (parsed.path or "/") + (("?" + parsed.query) if parsed.query else ""), headers=safe_headers)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                response.close()
                connection.close()
                if not location:
                    raise AddonHTTPError("Invalid redirect")
                target = urljoin(url, location)
                next_url, next_port = parse_public_url(target)
                if (next_url.scheme, next_url.hostname, next_port) != (parsed.scheme, parsed.hostname, port):
                    # Never disclose credentials to a different redirect origin.
                    headers = {k: v for k, v in (headers or {}).items() if k.lower() in ("range", "if-range", "user-agent")}
                url = target
                continue
            return connection, response, url
        except AddonHTTPError:
            raise
        except Exception:
            if "connection" in locals():
                connection.close()
            raise AddonHTTPError("Upstream connection failed") from None
    raise AddonHTTPError("Too many redirects")

def _json(url):
    connection, response, _ = open_public(url)
    try:
        if response.status != 200:
            raise AddonHTTPError("Upstream did not return JSON")
        chunks = []
        size = 0
        deadline = time.monotonic() + 15
        while True:
            if time.monotonic() > deadline:
                raise AddonHTTPError("Response deadline exceeded")
            chunk = response.read1(min(65536, MAX_JSON_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_JSON_BYTES:
                raise AddonHTTPError("Response exceeded size limit")
        content = b"".join(chunks)
        result = json.loads(content)
        if not isinstance(result, dict):
            raise AddonHTTPError("Invalid JSON object")
        return result
    except (ValueError, OSError):
        raise AddonHTTPError("Invalid upstream response") from None
    finally:
        response.close()
        connection.close()

async def fetch_json(url):
    return await asyncio.to_thread(_json, url)
