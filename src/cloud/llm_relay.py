# -*- coding: UTF-8 -*-
"""PC-side LLM relay: device -> (hdc rport tcp:16000) -> PC :16000 -> LLM cloud.

Plain-HTTP on the device side; TLS + cert-no-verify handled here on the PC.
Run: python llm_relay.py   (listens 127.0.0.1:16000)
"""
import http.server
import ssl
import urllib.error
import urllib.request

UPSTREAM = "https://api.rvcompute.com:60000"
HOP_HEADERS = ("host", "content-length", "accept-encoding", "connection",
               "transfer-encoding")


class Relay(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _relay(self):
        ln = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(ln) if ln else None
        req = urllib.request.Request(UPSTREAM + self.path, data=body,
                                     method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in HOP_HEADERS:
                req.add_header(k, v)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            r = urllib.request.urlopen(req, context=ctx, timeout=1200)
            data, code, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            data, code, hdrs = e.read(), e.code, dict(e.headers)
        except Exception as e:  # noqa: BLE001
            data = ('{"error":"relay failure: %s"}' % e).encode()
            code, hdrs = 502, {"Content-Type": "application/json"}
        self.send_response(code)
        for k, v in hdrs.items():
            if k.lower() not in HOP_HEADERS:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = _relay

    def log_message(self, fmt, *args):
        with open("relay-access.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 16000), Relay)
    print("llm relay on 127.0.0.1:16000 -> %s" % UPSTREAM, flush=True)
    srv.serve_forever()
