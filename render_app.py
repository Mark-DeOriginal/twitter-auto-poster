import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass


def main():
    for var in ("TELEGRAM_BOT_TOKEN", "NEWS_API_KEY", "GROQ_API_KEY"):
        if var not in os.environ:
            print(f"Missing required env var: {var}", file=sys.stderr)
            sys.exit(1)

    from bot import run_listener

    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"Health server running on port {port}")

    run_listener(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["NEWS_API_KEY"],
        os.environ["GROQ_API_KEY"],
    )


if __name__ == "__main__":
    main()
