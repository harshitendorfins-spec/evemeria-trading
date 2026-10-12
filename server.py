import http.server
import socketserver
import webbrowser
import os
import sys

PORT = 8080
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # Keep console output clean
        sys.stdout.write(f"[Kite Virtual F&O] {self.address_string()} - {format % args}\n")

def run():
    os.chdir(DIRECTORY)
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        url = f"http://127.0.0.1:{PORT}/index.html"
        print("=" * 60)
        print("🚀 Zerodha Kite Virtual F&O Trading Platform is running!")
        print(f"👉 Local URL: {url}")
        print("=" * 60)
        print("Press Ctrl+C to stop the server.\n")
        
        # Open in default browser
        webbrowser.open(url)
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")

if __name__ == "__main__":
    run()
