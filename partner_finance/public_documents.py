"""Fetch anonymous public reports without uploading a local, protected file."""
import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from pathlib import PurePosixPath
from urllib.parse import urlparse, urljoin, unquote, urldefrag

from lxml import html

MAX_BYTES = 25 * 1024 * 1024


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("사내망, 로컬, 예약 주소에는 연결하지 않습니다. 외부 공개 원문 주소를 넣어 주세요.")
    return addresses


def validate_target(url):
    if len(url) > 4096 or any(ord(c) < 32 for c in url):
        raise ValueError("유효한 공개 문서 주소를 넣어 주세요.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("http 또는 https로 시작하는 외부 공개 링크만 사용할 수 있습니다.")
    if parsed.username is not None or parsed.password is not None or parsed.port not in {None, 80, 443}:
        raise ValueError("로그인 정보나 별도 포트가 포함된 주소는 지원하지 않습니다.")
    public_addresses(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))


def _public_connection(address, timeout=30, source_address=None, **kwargs):
    # Connect to a validated numeric address, not a second DNS lookup (rebinding).
    host, port = address
    candidates = public_addresses(host, port)
    error = None
    for family, socktype, proto, _, sockaddr in candidates:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError("공개 원문 서버에 연결할 수 없습니다.")


class PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(PublicHTTPConnection, req)


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=self._context)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def document_links(content, base_url):
    page = html.fromstring(content, base_url=base_url)
    links = []
    seen = set()
    for anchor in page.xpath("//a[@href]"):
        url = urldefrag(urljoin(base_url, anchor.get("href")))[0]
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or parsed.username is not None:
            continue
        if PurePosixPath(parsed.path.lower()).suffix not in {".pdf", ".xlsx", ".csv"} or url in seen:
            continue
        seen.add(url)
        title = " ".join(anchor.text_content().split())[:180] or unquote(PurePosixPath(parsed.path).name)
        links.append({"url": url, "title": title})
        if len(links) >= 100:
            break
    return links


def fetch_public_document(url):
    url = url.strip()
    validate_target(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicHTTPHandler(),
                                        PublicHTTPSHandler(), PublicRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "PartnerReportReader/1.0", "Accept-Encoding": "identity"})
    try:
        with opener.open(request, timeout=30) as response:
            final_url = response.geturl()
            validate_target(final_url)
            mime = response.headers.get_content_type()
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("공개 문서가 25MB 다운로드 한도를 넘습니다. 더 작은 공식 문서 링크를 사용해 주세요.")
            content = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"원문 서버가 다운로드를 거부했습니다 (HTTP {exc.code}). 로그인이나 접근제한을 우회하지 않습니다. 다른 공식 공개 PDF 링크를 사용해 주세요.") from None
    except (urllib.error.URLError, OSError):
        raise ValueError("원문 서버에 연결하지 못했습니다. 주소와 공개 접근 가능 여부를 확인해 주세요.") from None
    if len(content) > MAX_BYTES:
        raise ValueError("공개 문서가 25MB 다운로드 한도를 넘습니다.")
    if not content:
        raise ValueError("원문 서버가 빈 파일을 반환했습니다.")
    name = unquote(PurePosixPath(urlparse(final_url).path).name) or "public_report"
    if content.lstrip().startswith(b"%PDF-"):
        name = name if name.lower().endswith(".pdf") else "public_report.pdf"
        mime = "application/pdf"
    elif mime == "text/html" or content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        links = document_links(content, final_url)
        if not links:
            raise ValueError("문서가 아닌 웹페이지입니다. PDF 다운로드 버튼의 링크 주소를 복사해 넣어 주세요. 자바스크립트나 로그인이 필요한 페이지는 자동 탐색하지 않습니다.")
        return {"kind": "links", "url": final_url, "links": links}
    elif name.lower().endswith(".xlsx") and content.startswith(b"PK"):
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif name.lower().endswith(".csv") and (mime.startswith("text/") or mime in {"application/csv", "application/octet-stream"}):
        mime = "text/csv"
    else:
        raise ValueError("공개 PDF, XLSX, CSV 원문만 지원합니다. 암호화되거나 다른 형식인 파일은 처리하지 않습니다.")
    return {"kind": "document", "url": final_url, "requested_url": url,
            "name": name, "type": mime, "content": content}
