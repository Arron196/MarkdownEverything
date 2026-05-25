import pytest
from fastapi import HTTPException

from app.services.url_security import assert_public_url
from app.services.url_security import is_blocked_address


def test_rejects_localhost_url():
    with pytest.raises(HTTPException):
        assert_public_url("http://localhost:8000")


def test_rejects_file_scheme():
    with pytest.raises(HTTPException):
        assert_public_url("file:///etc/passwd")


def test_allows_rfc2544_proxy_dns_address_for_domain():
    assert is_blocked_address("198.18.0.233", host_is_ip_literal=False) is False


def test_rejects_rfc2544_proxy_address_when_direct_ip_literal():
    assert is_blocked_address("198.18.0.233", host_is_ip_literal=True) is True
