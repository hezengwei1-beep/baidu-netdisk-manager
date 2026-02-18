#!/usr/bin/env python3
"""临时 HTTP 服务器：接收浏览器 POST 的 SRT 数据并保存到本地"""

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "subtitles"
RESULTS_FILE = Path(__file__).parent / "data" / "browser_export_results.json"

class SRTReceiver(BaseHTTPRequestHandler):
    all_results = []

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(body)

            if data.get('action') == 'save_batch':
                results = data.get('results', [])
                saved = 0
                for r in results:
                    if r.get('status') == 'ok' and r.get('srt'):
                        file_path = r['path']
                        srt = r['srt']
                        filename = file_path.rsplit('/', 1)[-1]
                        rel_dir = file_path.rsplit('/', 1)[0].lstrip('/')
                        save_dir = DATA_DIR / rel_dir
                        save_dir.mkdir(parents=True, exist_ok=True)
                        stem = Path(filename).stem

                        # 保存 SRT
                        (save_dir / f"{stem}.srt").write_text(srt, encoding='utf-8')

                        # 保存纯文本
                        lines = []
                        for line in srt.strip().split('\n'):
                            line = line.strip()
                            if not line or line.isdigit() or '-->' in line or '此字幕由AI自动生成' in line:
                                continue
                            lines.append(line)
                        text = '\n'.join(lines)
                        (save_dir / f"{stem}.txt").write_text(text, encoding='utf-8')

                        SRTReceiver.all_results.append({
                            'path': file_path,
                            'status': 'ok',
                            'srt_length': len(srt),
                            'text_length': len(text),
                        })
                        saved += 1

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                resp = json.dumps({'saved': saved, 'total_saved': len(SRTReceiver.all_results)})
                self.wfile.write(resp.encode())

            elif data.get('action') == 'done':
                # 保存汇总
                RESULTS_FILE.write_text(json.dumps(SRTReceiver.all_results, ensure_ascii=False, indent=2))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                resp = json.dumps({'total': len(SRTReceiver.all_results), 'file': str(RESULTS_FILE)})
                self.wfile.write(resp.encode())
                # 延迟关闭
                import threading
                threading.Timer(1.0, lambda: os._exit(0)).start()

            else:
                self.send_response(400)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

        except Exception as e:
            self.send_response(500)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(str(e).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[SRT] {args[0]}")

if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 18765), SRTReceiver)
    print('SRT Receiver 启动: http://127.0.0.1:18765')
    server.serve_forever()
