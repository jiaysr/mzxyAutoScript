# This Python file uses the following encoding: utf-8
"""无 GUI 依赖的实时画面查看器：内置 HTTP 服务，浏览器通过 MJPEG 看标注帧。

toolkit 内的 OpenCV 是 headless 版本，没有 cv2.imshow，也没有 tkinter，
所以测试脚本统一用这个浏览器查看器。
"""
import html
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 22268


class FrameViewer:
    def __init__(self, port: int = DEFAULT_PORT, title: str = "OAS 实时监测", subtitle: str = ""):
        self.stop_event = threading.Event()
        self._cond = threading.Condition()
        self._jpeg = None
        self._frame_id = 0
        self.title = html.escape(title)
        self.subtitle = html.escape(subtitle)
        self.index_html = self._build_html().encode("utf-8")
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _build_html(self) -> str:
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{self.title}</title>
<style>
html, body {{ margin: 0; background: #111; color: #ddd; font-family: system-ui, sans-serif; }}
header {{ display: flex; gap: 12px; align-items: center; padding: 8px 12px; }}
button {{ background: #333; color: #eee; border: 1px solid #555; padding: 4px 12px; border-radius: 4px; cursor: pointer; }}
button:hover {{ background: #444; }}
img {{ display: block; width: 100%; height: auto; }}
</style>
</head>
<body>
<header>
  <button onclick="fetch('/quit').then(() => document.body.innerHTML = '<p style=padding:12px>已停止</p>')">停止</button>
  <span>{self.title} {self.subtitle}</span>
</header>
<img src="/stream" alt="stream">
</body>
</html>
"""

    def _handler(self):
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = viewer.index_html
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    last_id = -1
                    try:
                        while not viewer.stop_event.is_set():
                            frame_id, jpeg = viewer.wait_frame(last_id)
                            if jpeg is None or frame_id == last_id:
                                continue
                            last_id = frame_id
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                            self.wfile.write(jpeg)
                            self.wfile.write(b"\r\n")
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                elif self.path == "/quit":
                    viewer.stop_event.set()
                    body = b"stopped"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        return Handler

    def start(self) -> str:
        self._thread.start()
        return self.url

    def update(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._frame_id += 1
            self._cond.notify_all()

    def wait_frame(self, last_id: int, timeout: float = 0.5):
        with self._cond:
            if self._frame_id == last_id:
                self._cond.wait(timeout)
            return self._frame_id, self._jpeg

    def stop(self) -> None:
        self.stop_event.set()
        with self._cond:
            self._cond.notify_all()
        self._server.shutdown()
        self._server.server_close()
