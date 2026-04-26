#!/usr/bin/env python3
from aiohttp import web
import os
import sys
import json
from aiohttp import ClientSession

WEB_ADMIN_PATH = os.path.join(os.path.dirname(__file__), 'web-admin')
INDEX_PATH = os.path.join(WEB_ADMIN_PATH, 'index.html')

if not os.path.exists(INDEX_PATH):
    print(f"❌ Ошибка: Файл '{INDEX_PATH}' не найден!")
    sys.exit(1)

async def index(request):
    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def proxy_api(request):
    method = request.method
    path = request.path
    backend_url = f"http://localhost:8080{path}"
    body = await request.read()
    headers = dict(request.headers)
    headers.pop('Host', None)
    
    async with ClientSession() as session:
        try:
            async with session.request(method=method, url=backend_url, headers=headers, data=body if body else None, params=request.query) as resp:
                response_body = await resp.read()
                return web.Response(status=resp.status, headers=dict(resp.headers), body=response_body)
        except Exception as e:
            return web.json_response({"status": "error", "message": f"Ошибка: {str(e)}"}, status=500)

async def proxy_websocket(request):
    from aiohttp import ClientSession
    import asyncio
    
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    username = request.query.get('username', '')
    
    try:
        async with ClientSession() as session:
            backend_ws_url = f"ws://localhost:8080/ws?username={username}"
            async with session.ws_connect(backend_ws_url) as ws_client:
                async def forward_to_backend():
                    async for msg in ws_server:
                        if msg.type == web.WSMsgType.TEXT:
                            await ws_client.send_str(msg.data)
                        elif msg.type == web.WSMsgType.CLOSE:
                            break
                
                async def forward_to_client():
                    async for msg in ws_client:
                        if msg.type == web.WSMsgType.TEXT:
                            await ws_server.send_str(msg.data)
                        elif msg.type == web.WSMsgType.CLOSE:
                            break
                
                await asyncio.gather(forward_to_backend(), forward_to_client())
    except Exception as e:
        print(f"WebSocket proxy error: {e}")
    
    return ws_server

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/index.html', index)
app.router.add_route('*', '/api/{tail:.*}', proxy_api)
app.router.add_get('/ws', proxy_websocket)

if __name__ == '__main__':
    print("=" * 50)
    print("🚀 Enigma Messenger - Веб-админ-панель")
    print("=" * 50)
    print(f"🌐 Админка доступна: http://localhost:3000")
    print("=" * 50)
    web.run_app(app, host='0.0.0.0', port=3000)