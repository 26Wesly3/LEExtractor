"""Run the bundled static demo locally with Python 3."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LEExtractor local demo')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent / 'dist'
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    try:
        with ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
            print(f'LEExtractor: http://127.0.0.1:{args.port}  (Ctrl+C to stop)')
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print('\nStopped.')
    except OSError as exc:
        parser.exit(1, f'Cannot start local server: {exc}\nTry --port 8001\n')
