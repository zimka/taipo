# encoding: utf-8
"""HTTPS client via Foundation NSURLSession (macOS system trust store)."""

import threading


class HTTPError(Exception):
    """Raised for HTTP status >= 400 (urllib-compatible surface)."""

    def __init__(self, code, body=b""):
        self.code = int(code)
        self.status_code = self.code
        self._body = body if isinstance(body, bytes) else bytes(body or b"")

    def read(self):
        return self._body

    def __str__(self):
        return "HTTP Error %s" % self.code


class HTTPResponse(object):
    def __init__(self, status_code, content, headers=None):
        self.status_code = int(status_code)
        self.content = content if isinstance(content, bytes) else bytes(content or b"")
        self.headers = dict(headers or {})

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")


def requests_get(url, headers=None, timeout=600):
    """GET request; returns HTTPResponse."""
    return _request("GET", url, data=None, headers=headers, timeout=timeout)


def requests_post(url, data=None, headers=None, timeout=600):
    """POST request with optional bytes body; returns HTTPResponse."""
    return _request("POST", url, data=data, headers=headers, timeout=timeout)


def _request(method, url, data=None, headers=None, timeout=600):
    try:
        from Foundation import (
            NSData,
            NSMutableURLRequest,
            NSURL,
            NSURLSession,
            NSURLSessionConfiguration,
        )
    except ImportError as exc:
        raise RuntimeError("HTTPS requires Glyphs (Foundation)") from exc

    ns_url = NSURL.URLWithString_(url)
    if ns_url is None:
        raise ValueError("Invalid URL: %r" % (url,))

    request = NSMutableURLRequest.requestWithURL_(ns_url)
    request.setHTTPMethod_(method)
    for key, value in (headers or {}).items():
        request.setValue_forHTTPHeaderField_(str(value), str(key))
    if data is not None:
        request.setHTTPBody_(NSData.dataWithBytes_length_(data, len(data)))

    config = NSURLSessionConfiguration.defaultSessionConfiguration()
    config.setTimeoutIntervalForRequest_(float(timeout))
    config.setTimeoutIntervalForResource_(float(timeout))
    session = NSURLSession.sessionWithConfiguration_(config)

    result = {"data": b"", "response": None, "error": None}
    event = threading.Event()

    def handler(data, response, error):
        if data is not None:
            result["data"] = bytes(data)
        result["response"] = response
        result["error"] = error
        event.set()

    task = session.dataTaskWithRequest_completionHandler_(request, handler)
    task.resume()

    wait_seconds = float(timeout) + 5.0
    if not event.wait(wait_seconds):
        task.cancel()
        raise TimeoutError("Request timed out after %s seconds" % timeout)

    error = result["error"]
    if error is not None:
        raise RuntimeError("HTTP request failed: %s" % error)

    response = result["response"]
    body = result["data"]
    status_code = 0
    header_dict = {}

    if response is not None:
        status_code = int(response.statusCode())
        fields = response.allHeaderFields()
        if fields is not None:
            for key in fields:
                header_dict[str(key)] = str(fields[key])

    if status_code >= 400:
        raise HTTPError(status_code, body)

    return HTTPResponse(status_code, body, header_dict)
