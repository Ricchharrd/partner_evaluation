import socket
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch

from partner_finance.public_documents import (fetch_public_document, validate_target,
    document_links, PublicRedirectHandler, _public_connection, MAX_BYTES)


PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


class PublicDocumentTests(unittest.TestCase):
    def test_private_and_mixed_dns_are_blocked(self):
        for answers in (PRIVATE, PUBLIC + PRIVATE):
            with patch("socket.getaddrinfo", return_value=answers):
                with self.assertRaises(ValueError):
                    validate_target("https://example.com/report.pdf")

    def test_credentials_local_schemes_and_ports_are_blocked(self):
        for url in ("file:///private.pdf", "https://user:password@example.com/file.pdf",
                    "http://example.com:8080/file.pdf", "https://example.com/\nfile.pdf"):
            with self.assertRaises(ValueError):
                validate_target(url)

    def test_connection_rechecks_dns_and_pins_numeric_address(self):
        with patch("socket.getaddrinfo", return_value=PUBLIC), patch("socket.socket") as sock:
            _public_connection(("example.com", 443), timeout=30)
            sock.return_value.connect.assert_called_once_with(("93.184.216.34", 443))
        with patch("socket.getaddrinfo", return_value=PRIVATE), patch("socket.socket") as sock:
            with self.assertRaises(ValueError):
                _public_connection(("example.com", 443))
            sock.assert_not_called()

    def test_redirect_to_private_network_is_blocked(self):
        with patch("socket.getaddrinfo", return_value=PRIVATE):
            with self.assertRaises(ValueError):
                PublicRedirectHandler().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/file.pdf")

    def response(self, data, mime="application/pdf", url="https://example.com/download?id=1"):
        response = MagicMock()
        response.headers = Message()
        response.headers["Content-Type"] = mime
        response.geturl.return_value = url
        response.read.return_value = data
        return response

    def test_pdf_endpoint_without_extension_is_recognized(self):
        response = self.response(b"%PDF-1.7 public test")
        with patch("socket.getaddrinfo", return_value=PUBLIC), patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value = response
            result = fetch_public_document("https://example.com/download?id=1")
        self.assertEqual(result["name"], "public_report.pdf")
        self.assertEqual(result["url"], "https://example.com/download?id=1")
        self.assertEqual(result["content"], b"%PDF-1.7 public test")

    def test_html_yields_candidates_not_financial_facts(self):
        page = b'<html><a href="/report.pdf">Annual report 2025</a><a href="/report.pdf#page=2">Again</a></html>'
        response = self.response(page, "text/html", "https://example.com/ir")
        with patch("socket.getaddrinfo", return_value=PUBLIC), patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value = response
            result = fetch_public_document("https://example.com/ir")
        self.assertEqual(result["kind"], "links")
        self.assertEqual(result["links"], [{"url": "https://example.com/report.pdf", "title": "Annual report 2025"}])

    def test_oversized_and_unsupported_files_are_rejected(self):
        for data, size in ((b"%PDF", MAX_BYTES + 1), (b"DRM encrypted file", None)):
            response = self.response(data)
            if size:
                response.headers["Content-Length"] = str(size)
            with patch("socket.getaddrinfo", return_value=PUBLIC), patch("urllib.request.build_opener") as build:
                build.return_value.open.return_value.__enter__.return_value = response
                with self.assertRaises(ValueError):
                    fetch_public_document("https://example.com/report.pdf")
