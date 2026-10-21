"""HTTP listeners use the bound address without reverse-DNS startup lookups."""
from http.server import ThreadingHTTPServer as _ThreadingHTTPServer
from socketserver import TCPServer


class ThreadingHTTPServer(_ThreadingHTTPServer):
    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
