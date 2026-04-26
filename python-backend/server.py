import asyncio
import json
import hashlib
import os
import sys
import random
import string
from datetime import datetime
from aiohttp import web
import aiomysql

# ========== КОНФИГУРАЦИЯ MYSQL ==========
DB_CONFIG = {
    'host': 'localhost',
    'user': 'enigma_user',
    'password': 'Enigma123!',
    'db': 'enigma_messenger',
    'charset': 'utf8mb4',
    'autocommit': True
}

# ========== CORS ==========
def cors_response(data=None, status=200):
    response = web.json_response(data, status=status)
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

async def options_handler(request):
    response = web.Response()
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
db_pool = None
active_connections = {}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def add_log(username, action, target, details=''):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO logs (username, action, target, details, timestamp)
                VALUES (%s, %s, %s, %s, %s)
            """, (username, action, target, details, datetime.now()))

async def add_punishment(username, type, reason, issued_by):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO punishments (username, type, reason, issued_by, issued_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (username, type, reason, issued_by, datetime.now()))

async def resolve_punishment(username, type, resolved_by):
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE punishments SET resolved=1, resolved_at=%s, resolved_by=%s 
                WHERE username=%s AND type=%s AND resolved=0
            """, (datetime.now(), resolved_by, username, type))

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
async def init_db_pool():
    global db_pool
    db_pool = await aiomysql.create_pool(**DB_CONFIG, minsize=1, maxsize=10)
    print("✅ База данных MySQL готова")

# ========== API ЛОГИН/РЕГИСТРАЦИЯ ==========

async def api_login(request):
    try:
        data = await request.json()
        username = data.get('username')
        password = data.get('password')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT password_hash, role, display_name, is_verified, avatar, account_status, ban_reason, warnings_count, has_active_warning
                    FROM users WHERE username=%s
                """, (username,))
                user = await cur.fetchone()
                
                if not user or user[0] != hash_password(password):
                    return cors_response({"status": "error", "message": "Неверный логин или пароль"})
                
                if user[5] == 'deactivated':
                    return cors_response({"status": "error", "message": "Аккаунт деактивирован", "account_status": "deactivated", "ban_reason": user[6]})
                if user[5] == 'deleted':
                    return cors_response({"status": "error", "message": "Аккаунт удален", "account_status": "deleted", "ban_reason": user[6]})
                
                await cur.execute("UPDATE users SET last_seen=%s WHERE username=%s", (datetime.now(), username))
                
                return cors_response({
                    "status": "success",
                    "role": user[1],
                    "display_name": user[2],
                    "is_verified": user[3],
                    "avatar": user[4] or '',
                    "account_status": user[5] or 'active',
                    "ban_reason": user[6] or '',
                    "warnings_count": user[7] or 0,
                    "has_active_warning": user[8] or 0
                })
    except Exception as e:
        print(f"Login error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_register(request):
    try:
        data = await request.json()
        username = data.get('username')
        display_name = data.get('display_name', username)
        password = data.get('password')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT username FROM users WHERE username=%s", (username,))
                if await cur.fetchone():
                    return cors_response({"status": "error", "message": "Пользователь уже существует"})
                
                await cur.execute("SELECT COUNT(*) FROM users")
                count = (await cur.fetchone())[0]
                role = 'owner' if count == 0 else 'user'
                
                await cur.execute("""
                    INSERT INTO users (username, display_name, password_hash, role, is_verified, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (username, display_name, hash_password(password), role, 1 if role == 'owner' else 0, datetime.now()))
                
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Register error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_change_password(request):
    try:
        data = await request.json()
        username = data.get('username')
        old_password = data.get('old_password')
        new_password = data.get('new_password')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT password_hash FROM users WHERE username=%s", (username,))
                user = await cur.fetchone()
                if not user or user[0] != hash_password(old_password):
                    return cors_response({"status": "error", "message": "Неверный текущий пароль"})
                
                if len(new_password) < 4:
                    return cors_response({"status": "error", "message": "Пароль должен быть минимум 4 символа"})
                
                await cur.execute("UPDATE users SET password_hash=%s WHERE username=%s", (hash_password(new_password), username))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Change password error: {e}")
        return cors_response({"status": "error"})

async def api_update_profile(request):
    try:
        data = await request.json()
        username = data.get('username')
        display_name = data.get('display_name')
        phone = data.get('phone', '')
        birth_date = data.get('birth_date', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE users SET display_name=%s, phone=%s, birth_date=%s WHERE username=%s
                """, (display_name, phone, birth_date if birth_date else None, username))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Update profile error: {e}")
        return cors_response({"status": "error"})

async def api_update_avatar(request):
    try:
        data = await request.json()
        username = data.get('username')
        avatar = data.get('avatar', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE users SET avatar=%s WHERE username=%s", (avatar, username))
                
                for contact in active_connections:
                    try:
                        await active_connections[contact].send_json({
                            "type": "avatar_updated",
                            "username": username,
                            "avatar": avatar
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Update avatar error: {e}")
        return cors_response({"status": "error"})

async def api_get_contacts(request):
    try:
        username = request.match_info['username']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT DISTINCT 
                        CASE 
                            WHEN from_user = %s THEN to_user 
                            ELSE from_user 
                        END as contact
                    FROM messages 
                    WHERE (from_user = %s OR to_user = %s) AND deleted = 0
                """, (username, username, username))
                chats_data = await cur.fetchall()
                
                contacts = []
                for chat in chats_data:
                    contact = chat[0]
                    if contact and contact != username and contact != 'System Support':
                        await cur.execute("SELECT display_name, avatar, is_verified FROM users WHERE username=%s", (contact,))
                        user_data = await cur.fetchone()
                        contacts.append({
                            "username": contact,
                            "display_name": user_data[0] if user_data else contact,
                            "avatar": user_data[1] if user_data and user_data[1] else '',
                            "is_verified": user_data[2] if user_data else 0
                        })
                return cors_response({"contacts": contacts})
    except Exception as e:
        print(f"Get contacts error: {e}")
        return cors_response({"contacts": []})

async def api_search_users(request):
    try:
        query = request.query.get('q', '')
        current = request.query.get('current', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT username, display_name, is_verified, avatar 
                    FROM users 
                    WHERE username != %s AND username != 'System Support' 
                    AND account_status = 'active'
                    AND (username LIKE %s OR display_name LIKE %s)
                    LIMIT 20
                """, (current, f'%{query}%', f'%{query}%'))
                users = await cur.fetchall()
                return cors_response([{"username": u[0], "display_name": u[1], "is_verified": u[2], "avatar": u[3] or ''} for u in users])
    except Exception as e:
        print(f"Search users error: {e}")
        return cors_response([])

# ========== СООБЩЕНИЯ ==========

async def api_get_messages(request):
    try:
        chat_id = request.match_info['chat_id']
        current_user = request.query.get('user', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, message, file_type, file_data, timestamp, delivered, read_status 
                    FROM messages 
                    WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) AND deleted=0
                    ORDER BY timestamp
                """, (current_user, chat_id, chat_id, current_user))
                messages = await cur.fetchall()
                
                return cors_response([{
                    "id": m[0], "from_user": m[1], "message": m[2],
                    "file_type": m[3] or '', "file_data": m[4] or '',
                    "timestamp": m[5].isoformat() if m[5] else '',
                    "delivered": m[6], "read_status": m[7]
                } for m in messages])
    except Exception as e:
        print(f"Get messages error: {e}")
        return cors_response([])

async def api_send(request):
    try:
        data = await request.json()
        from_user = data.get('from')
        to_user = data.get('to')
        text = data.get('text', '')
        file_type = data.get('file_type', 'text')
        file_data = data.get('file_data', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT blocked_users FROM users WHERE username=%s", (to_user,))
                blocked_by = await cur.fetchone()
                if blocked_by:
                    blocked_list = json.loads(blocked_by[0] or '[]')
                    if from_user in blocked_list:
                        return cors_response({"status": "error", "message": "Вы заблокированы этим пользователем"})
                
                await cur.execute("SELECT blocked_users FROM users WHERE username=%s", (from_user,))
                blocked_from = await cur.fetchone()
                if blocked_from:
                    blocked_list = json.loads(blocked_from[0] or '[]')
                    if to_user in blocked_list:
                        return cors_response({"status": "error", "message": "Вы заблокировали этого пользователя"})
                
                await cur.execute("""
                    INSERT INTO messages (from_user, to_user, message, file_type, file_data, timestamp, delivered, read_status) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (from_user, to_user, text, file_type, file_data, datetime.now(), 1 if to_user in active_connections else 0, 0))
                msg_id = cur.lastrowid
                
                if to_user in active_connections:
                    try:
                        await active_connections[to_user].send_json({
                            "type": "new_message",
                            "id": msg_id,
                            "from": from_user,
                            "text": text,
                            "file_type": file_type,
                            "file_data": file_data,
                            "timestamp": datetime.now().isoformat()
                        })
                    except:
                        pass
                
                return cors_response({"status": "success", "id": msg_id})
    except Exception as e:
        print(f"Send error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_send_group(request):
    try:
        data = await request.json()
        from_user = data.get('from')
        group_id = data.get('group_id')
        text = data.get('text', '')
        file_type = data.get('file_type', 'text')
        file_data = data.get('file_data', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT members, owner FROM `groups` WHERE id=%s", (group_id,))
                group = await cur.fetchone()
                if not group:
                    return cors_response({"status": "error", "message": "Группа не найдена"})
                
                members = json.loads(group[0])
                if from_user not in members:
                    return cors_response({"status": "error", "message": "Вы не участник этой группы"})
                
                # Сохраняем сообщение
                await cur.execute("""
                    INSERT INTO messages (from_user, to_user, message, file_type, file_data, timestamp, delivered, read_status) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (from_user, group_id, text, file_type, file_data, datetime.now(), 1, 0))
                msg_id = cur.lastrowid
                
                # Рассылаем всем участникам
                for member in members:
                    if member in active_connections:
                        try:
                            await active_connections[member].send_json({
                                "type": "new_message",
                                "id": msg_id,
                                "from": from_user,
                                "text": text,
                                "file_type": file_type,
                                "file_data": file_data,
                                "timestamp": datetime.now().isoformat(),
                                "is_group": True,
                                "group_id": group_id
                            })
                        except:
                            pass
                
                return cors_response({"status": "success", "id": msg_id})
    except Exception as e:
        print(f"Send group error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_edit_message(request):
    try:
        data = await request.json()
        message_id = data.get('message_id')
        text = data.get('text')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE messages SET message=%s WHERE id=%s", (text, message_id))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Edit message error: {e}")
        return cors_response({"status": "error"})

async def api_delete_message(request):
    try:
        data = await request.json()
        message_id = data.get('message_id')
        for_all = data.get('for_all', False)
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE messages SET deleted=1 WHERE id=%s", (message_id,))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Delete message error: {e}")
        return cors_response({"status": "error"})

async def api_mark_delivered(request):
    try:
        data = await request.json()
        message_id = data.get('message_id')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE messages SET delivered=1 WHERE id=%s", (message_id,))
                return cors_response({"status": "success"})
    except Exception as e:
        return cors_response({"status": "error"})

async def api_mark_read(request):
    try:
        data = await request.json()
        message_id = data.get('message_id')
        from_user = data.get('from_user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE messages SET read_status=1 WHERE id=%s", (message_id,))
                
                if from_user in active_connections:
                    try:
                        await active_connections[from_user].send_json({
                            "type": "message_read",
                            "message_id": message_id
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Mark read error: {e}")
        return cors_response({"status": "error"})

async def api_mark_chat_read(request):
    try:
        data = await request.json()
        username = data.get('username')
        chat_with = data.get('chat_with')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE messages SET read_status=1 WHERE from_user=%s AND to_user=%s
                """, (chat_with, username))
                return cors_response({"status": "success"})
    except Exception as e:
        return cors_response({"status": "error"})

# ========== ГРУППЫ ==========

async def api_get_groups(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id, name, owner, members FROM `groups`")
                groups = await cur.fetchall()
                return cors_response([{"id": g[0], "name": g[1], "owner": g[2], "members": g[3]} for g in groups])
    except Exception as e:
        print(f"Get groups error: {e}")
        return cors_response([])

async def api_create_group(request):
    try:
        data = await request.json()
        group_id = 'GRP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        name = data.get('name')
        owner = data.get('owner')
        members = list(set([owner] + data.get('members', [])))
        members_json = json.dumps(members)
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO `groups` (id, name, owner, created_at, members) 
                    VALUES (%s, %s, %s, %s, %s)
                """, (group_id, name, owner, datetime.now(), members_json))
                await add_log(owner, 'create_group', group_id, f'Создал группу {name}')
                return cors_response({"status": "success", "group_id": group_id})
    except Exception as e:
        print(f"Create group error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_rename_group(request):
    try:
        data = await request.json()
        group_id = data.get('group_id')
        new_name = data.get('name')
        current_user = data.get('current_user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT owner FROM `groups` WHERE id=%s", (group_id,))
                group = await cur.fetchone()
                if not group:
                    return cors_response({"status": "error", "message": "Группа не найдена"})
                if current_user != group[0]:
                    return cors_response({"status": "error", "message": "Только создатель группы может переименовывать её"})
                
                await cur.execute("UPDATE `groups` SET name=%s WHERE id=%s", (new_name, group_id))
                await add_log(current_user, 'rename_group', group_id, f'Переименовал в {new_name}')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Rename group error: {e}")
        return cors_response({"status": "error"})

async def api_add_group_member(request):
    try:
        data = await request.json()
        group_id = data.get('group_id')
        username = data.get('username')
        current_user = data.get('current_user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT members, owner FROM `groups` WHERE id=%s", (group_id,))
                group = await cur.fetchone()
                if not group:
                    return cors_response({"status": "error", "message": "Группа не найдена"})
                if current_user != group[1]:
                    return cors_response({"status": "error", "message": "Только создатель группы может добавлять участников"})
                
                members = json.loads(group[0])
                if username not in members:
                    members.append(username)
                    await cur.execute("UPDATE `groups` SET members=%s WHERE id=%s", (json.dumps(members), group_id))
                    await add_log(current_user, 'add_group_member', group_id, f'Добавил {username}')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Add group member error: {e}")
        return cors_response({"status": "error"})

async def api_remove_group_member(request):
    try:
        data = await request.json()
        group_id = data.get('group_id')
        username = data.get('username')
        current_user = data.get('current_user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT members, owner FROM `groups` WHERE id=%s", (group_id,))
                group = await cur.fetchone()
                if not group:
                    return cors_response({"status": "error", "message": "Группа не найдена"})
                if current_user != group[1]:
                    return cors_response({"status": "error", "message": "Только создатель группы может удалять участников"})
                
                members = json.loads(group[0])
                if username in members and username != group[1]:
                    members.remove(username)
                    await cur.execute("UPDATE `groups` SET members=%s WHERE id=%s", (json.dumps(members), group_id))
                    await add_log(current_user, 'remove_group_member', group_id, f'Удалил {username}')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Remove group member error: {e}")
        return cors_response({"status": "error"})

async def api_leave_group(request):
    try:
        group_id = request.match_info['group_id']
        username = request.query.get('user', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT members, owner FROM `groups` WHERE id=%s", (group_id,))
                group = await cur.fetchone()
                if not group:
                    return cors_response({"status": "error", "message": "Группа не найдена"})
                
                members = json.loads(group[0])
                if username in members:
                    members.remove(username)
                    await cur.execute("UPDATE `groups` SET members=%s WHERE id=%s", (json.dumps(members), group_id))
                    await add_log(username, 'leave_group', group_id, 'Покинул группу')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Leave group error: {e}")
        return cors_response({"status": "error"})

async def api_get_group_info(request):
    try:
        group_id = request.match_info['group_id']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id, name, owner, members, created_at FROM `groups` WHERE id=%s", (group_id,))
                group = await cur.fetchone()
                if group:
                    members = json.loads(group[3])
                    members_data = []
                    for m in members:
                        await cur.execute("SELECT display_name, avatar, is_verified, last_seen FROM users WHERE username=%s", (m,))
                        user = await cur.fetchone()
                        members_data.append({
                            "username": m,
                            "display_name": user[0] if user else m,
                            "avatar": user[1] if user else '',
                            "is_verified": user[2] if user else 0,
                            "last_seen": user[3].isoformat() if user and user[3] else ''
                        })
                    return cors_response({
                        "id": group[0],
                        "name": group[1],
                        "owner": group[2],
                        "members": members_data,
                        "created_at": group[4].isoformat() if group[4] else ''
                    })
                return cors_response({"status": "error"})
    except Exception as e:
        print(f"Get group info error: {e}")
        return cors_response({"status": "error"})

# ========== СТАТИСТИКА И ИЗБРАННОЕ ==========

async def api_get_chat_stats(request):
    try:
        chat_id = request.match_info['chat_id']
        current_user = request.query.get('user', '')
        filter_type = request.query.get('type', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                if filter_type == 'favorite':
                    await cur.execute("""
                        SELECT m.id, m.from_user, m.message, m.file_type, m.file_data, m.timestamp 
                        FROM messages m
                        JOIN favorites f ON f.message_id = m.id
                        WHERE f.user_from = %s AND ((m.from_user=%s AND m.to_user=%s) OR (m.from_user=%s AND m.to_user=%s)) AND m.deleted=0
                    """, (current_user, current_user, chat_id, chat_id, current_user))
                elif filter_type == 'photo':
                    await cur.execute("""
                        SELECT id, from_user, message, file_type, file_data, timestamp 
                        FROM messages 
                        WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                        AND file_type='photo' AND deleted=0
                        ORDER BY timestamp DESC
                    """, (current_user, chat_id, chat_id, current_user))
                elif filter_type == 'video':
                    await cur.execute("""
                        SELECT id, from_user, message, file_type, file_data, timestamp 
                        FROM messages 
                        WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                        AND file_type='video' AND deleted=0
                        ORDER BY timestamp DESC
                    """, (current_user, chat_id, chat_id, current_user))
                elif filter_type == 'gif':
                    await cur.execute("""
                        SELECT id, from_user, message, file_type, file_data, timestamp 
                        FROM messages 
                        WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                        AND file_type='gif' AND deleted=0
                        ORDER BY timestamp DESC
                    """, (current_user, chat_id, chat_id, current_user))
                elif filter_type == 'link':
                    await cur.execute("""
                        SELECT id, from_user, message, file_type, file_data, timestamp 
                        FROM messages 
                        WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                        AND (message LIKE '%%http%%' OR message LIKE '%%www.%%') AND deleted=0
                        ORDER BY timestamp DESC
                    """, (current_user, chat_id, chat_id, current_user))
                else:
                    return cors_response([])
                
                items = await cur.fetchall()
                result = []
                for item in items:
                    result.append({
                        "id": item[0],
                        "from": item[1],
                        "message": item[2],
                        "file_type": item[3] or '',
                        "file_data": item[4] or '',
                        "timestamp": item[5].isoformat() if item[5] else ''
                    })
                return cors_response(result)
    except Exception as e:
        print(f"Get chat stats error: {e}")
        return cors_response([])

async def api_get_chat_stat_counts(request):
    try:
        chat_id = request.match_info['chat_id']
        current_user = request.query.get('user', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Подсчёт favourites
                await cur.execute("""
                    SELECT COUNT(*) FROM favorites f
                    JOIN messages m ON m.id = f.message_id
                    WHERE f.user_from = %s AND ((m.from_user=%s AND m.to_user=%s) OR (m.from_user=%s AND m.to_user=%s))
                """, (current_user, current_user, chat_id, chat_id, current_user))
                favorites = (await cur.fetchone())[0]
                
                # Подсчёт фото
                await cur.execute("""
                    SELECT COUNT(*) FROM messages 
                    WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                    AND file_type='photo' AND deleted=0
                """, (current_user, chat_id, chat_id, current_user))
                photos = (await cur.fetchone())[0]
                
                # Подсчёт видео
                await cur.execute("""
                    SELECT COUNT(*) FROM messages 
                    WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                    AND file_type='video' AND deleted=0
                """, (current_user, chat_id, chat_id, current_user))
                videos = (await cur.fetchone())[0]
                
                # Подсчёт GIF
                await cur.execute("""
                    SELECT COUNT(*) FROM messages 
                    WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                    AND file_type='gif' AND deleted=0
                """, (current_user, chat_id, chat_id, current_user))
                gifs = (await cur.fetchone())[0]
                
                # Подсчёт ссылок
                await cur.execute("""
                    SELECT COUNT(*) FROM messages 
                    WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) 
                    AND (message LIKE '%%http%%' OR message LIKE '%%www.%%') AND deleted=0
                """, (current_user, chat_id, chat_id, current_user))
                links = (await cur.fetchone())[0]
                
                return cors_response({
                    "favorites": favorites,
                    "photos": photos,
                    "videos": videos,
                    "gifs": gifs,
                    "links": links
                })
    except Exception as e:
        print(f"Get chat stat counts error: {e}")
        return cors_response({})

async def api_add_to_favorites(request):
    try:
        data = await request.json()
        user_from = data.get('user_from')
        message_id = data.get('message_id')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO favorites (user_from, message_id, timestamp) 
                    VALUES (%s, %s, %s)
                """, (user_from, message_id, datetime.now()))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Add to favorites error: {e}")
        return cors_response({"status": "error"})

# ========== ПОЛЬЗОВАТЕЛИ (БЛОКИРОВКИ, СТАТУС) ==========

async def api_get_user_status(request):
    try:
        username = request.match_info['username']
        is_online = username in active_connections
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT last_seen FROM users WHERE username=%s", (username,))
                last_seen = await cur.fetchone()
                return cors_response({
                    "username": username,
                    "online": is_online,
                    "last_seen": last_seen[0].isoformat() if last_seen and last_seen[0] else None
                })
    except Exception as e:
        return cors_response({"online": False})

async def api_get_user_full_info(request):
    try:
        username = request.match_info['username']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT display_name, avatar, is_verified, last_seen FROM users WHERE username=%s", (username,))
                user = await cur.fetchone()
                if user:
                    return cors_response({
                        "display_name": user[0],
                        "avatar": user[1] or '',
                        "is_verified": user[2],
                        "last_seen": user[3].isoformat() if user[3] else ''
                    })
                return cors_response({"status": "error"})
    except Exception as e:
        print(f"Get user full info error: {e}")
        return cors_response({"status": "error"})

async def api_block_user(request):
    try:
        data = await request.json()
        username = data.get('username')
        block_user = data.get('block_user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT blocked_users FROM users WHERE username=%s", (username,))
                user = await cur.fetchone()
                blocked_list = json.loads(user[0] or '[]')
                if block_user not in blocked_list:
                    blocked_list.append(block_user)
                    await cur.execute("UPDATE users SET blocked_users=%s WHERE username=%s", (json.dumps(blocked_list), username))
                    await add_log(username, 'block_user', block_user, 'Заблокировал пользователя')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Block user error: {e}")
        return cors_response({"status": "error"})

async def api_unblock_user(request):
    try:
        data = await request.json()
        username = data.get('username')
        unblock_user = data.get('unblock_user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT blocked_users FROM users WHERE username=%s", (username,))
                user = await cur.fetchone()
                blocked_list = json.loads(user[0] or '[]')
                if unblock_user in blocked_list:
                    blocked_list.remove(unblock_user)
                    await cur.execute("UPDATE users SET blocked_users=%s WHERE username=%s", (json.dumps(blocked_list), username))
                    await add_log(username, 'unblock_user', unblock_user, 'Разблокировал пользователя')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Unblock user error: {e}")
        return cors_response({"status": "error"})

async def api_is_blocked(request):
    try:
        username = request.match_info['username']
        target = request.query.get('target', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT blocked_users FROM users WHERE username=%s", (username,))
                user = await cur.fetchone()
                blocked_list = json.loads(user[0] or '[]')
                return cors_response({"is_blocked": target in blocked_list})
    except Exception as e:
        return cors_response({"is_blocked": False})

async def api_send_notification(request):
    try:
        data = await request.json()
        to_user = data.get('to_user')
        message = data.get('message')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO messages (from_user, to_user, message, timestamp, delivered) 
                    VALUES (%s, %s, %s, %s, %s)
                """, ('System Support', to_user, message, datetime.now(), 1))
                
                if to_user in active_connections:
                    try:
                        await active_connections[to_user].send_json({
                            "type": "new_message",
                            "from": "🛡️ Уведомления",
                            "text": message,
                            "timestamp": datetime.now().isoformat()
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Send notification error: {e}")
        return cors_response({"status": "error"})

# ========== ТИКЕТЫ ==========

async def api_create_ticket(request):
    try:
        data = await request.json()
        ticket_id = 'TKT-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        title = data.get('title')
        from_user = data.get('from_user')
        message = data.get('message')
        messages_json = json.dumps([{"from": from_user, "message": message, "timestamp": datetime.now().isoformat()}])
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO tickets (id, from_user, title, messages, status, created_at, updated_at) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (ticket_id, from_user, title, messages_json, 'open', datetime.now(), datetime.now()))
                await add_log(from_user, 'create_ticket', ticket_id, title)
                return cors_response({"status": "success", "ticket_id": ticket_id})
    except Exception as e:
        print(f"Create ticket error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_get_user_tickets(request):
    try:
        username = request.match_info['username']
        archived = request.query.get('archived', '0')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, title, status, created_at, messages, archived, rated 
                    FROM tickets WHERE from_user=%s AND archived=%s 
                    ORDER BY created_at DESC
                """, (username, archived))
                tickets = await cur.fetchall()
                return cors_response([{
                    "id": t[0], "title": t[1], "status": t[2],
                    "created_at": t[3].isoformat() if t[3] else '',
                    "messages": t[4], "archived": t[5], "rated": t[6] or 0
                } for t in tickets])
    except Exception as e:
        print(f"Get tickets error: {e}")
        return cors_response([])

async def api_get_ticket(request):
    try:
        ticket_id = request.match_info['ticket_id']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, title, messages, status, created_at, archived, rated 
                    FROM tickets WHERE id=%s
                """, (ticket_id,))
                ticket = await cur.fetchone()
                if ticket:
                    return cors_response({
                        "id": ticket[0], "from_user": ticket[1], "title": ticket[2],
                        "messages": ticket[3], "status": ticket[4],
                        "created_at": ticket[5].isoformat() if ticket[5] else '',
                        "archived": ticket[6], "rated": ticket[7] or 0
                    })
                return cors_response({"status": "error"})
    except Exception as e:
        print(f"Get ticket error: {e}")
        return cors_response({"status": "error"})

async def api_ticket_reply(request):
    try:
        data = await request.json()
        ticket_id = data.get('ticket_id')
        from_user = data.get('from_user')
        message = data.get('message')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT messages, status, archived FROM tickets WHERE id=%s", (ticket_id,))
                ticket = await cur.fetchone()
                if ticket and ticket[1] != 'closed' and ticket[2] != 1:
                    messages_list = json.loads(ticket[0])
                    messages_list.append({"from": from_user, "message": message, "timestamp": datetime.now().isoformat()})
                    await cur.execute("""
                        UPDATE tickets SET messages=%s, status='replied', updated_at=%s WHERE id=%s
                    """, (json.dumps(messages_list), datetime.now(), ticket_id))
                    await add_log(from_user, 'ticket_reply', ticket_id, message[:50])
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Ticket reply error: {e}")
        return cors_response({"status": "error"})

async def api_rate_support(request):
    try:
        data = await request.json()
        rating = data.get('rating')
        ticket_id = data.get('ticket_id')
        comment = data.get('comment', '')
        rated_by = data.get('rated_by', 'user')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO support_ratings (ticket_id, rating, comment, rated_by, timestamp) 
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE rating=%s, comment=%s, rated_by=%s, timestamp=%s
                """, (ticket_id, rating, comment, rated_by, datetime.now(), rating, comment, rated_by, datetime.now()))
                await cur.execute("UPDATE tickets SET rated=1 WHERE id=%s", (ticket_id,))
                await add_log(rated_by, 'rate_support', ticket_id, f'Оценка {rating}')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Rate error: {e}")
        return cors_response({"status": "error"})

# ========== БАГ-РЕПОРТЫ ==========

async def api_bug_report(request):
    try:
        data = await request.json()
        bug_type = data.get('bug_type')
        message = data.get('message')
        from_user = data.get('from')
        
        priority_map = {'improvement': 'medium', 'error': 'high', 'vulnerability': 'blocker'}
        priority = priority_map.get(bug_type, 'medium')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO bug_reports (from_user, bug_type, message, priority, status, timestamp) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (from_user, bug_type, message, priority, 'new', datetime.now()))
                await add_log(from_user, 'bug_report', bug_type, message[:50])
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Bug report error: {e}")
        return cors_response({"status": "error"})

# ========== ЖАЛОБЫ ==========

async def api_create_complaint(request):
    try:
        data = await request.json()
        from_user = data.get('from_user')
        type = data.get('type', 'user')
        target = data.get('target_user') or data.get('target_chat')
        reason = data.get('reason')
        details = data.get('details', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO complaints (from_user, type, target, reason, details, status, timestamp) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (from_user, type, target, reason, details, 'new', datetime.now()))
                await add_log(from_user, 'complaint', target, reason)
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Create complaint error: {e}")
        return cors_response({"status": "error"})

# ========== АДМИН API ==========

async def api_admin_users(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT username, display_name, role, is_verified, avatar, account_status, ban_reason, warnings_count, has_active_warning 
                    FROM users WHERE username != 'System Support'
                """)
                users = await cur.fetchall()
                return cors_response([{
                    "username": u[0], "display_name": u[1], "role": u[2], "is_verified": u[3],
                    "avatar": u[4] or '', "account_status": u[5] or 'active', "ban_reason": u[6] or '',
                    "warnings_count": u[7] or 0, "has_active_warning": u[8] or 0
                } for u in users])
    except Exception as e:
        print(f"Admin users error: {e}")
        return cors_response([])

async def api_admin_user_full_info(request):
    try:
        username = request.match_info['username']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT display_name, role, is_verified, created_at, warnings_count, has_active_warning, 
                           avatar, ban_reason, phone, birth_date, last_seen, account_status 
                    FROM users WHERE username=%s
                """, (username,))
                user = await cur.fetchone()
                if user:
                    # Получаем активное предупреждение
                    await cur.execute("""
                        SELECT reason, issued_by, issued_at FROM warnings 
                        WHERE username=%s AND resolved=0 ORDER BY issued_at DESC LIMIT 1
                    """, (username,))
                    warning = await cur.fetchone()
                    warning_info = None
                    if warning:
                        warning_info = {"reason": warning[0], "issued_by": warning[1], "issued_at": warning[2].isoformat() if warning[2] else ''}
                    
                    return cors_response({
                        "display_name": user[0], "role": user[1], "is_verified": user[2],
                        "created_at": user[3].isoformat() if user[3] else '',
                        "warnings_count": user[4] or 0, "has_active_warning": user[5] or 0,
                        "avatar": user[6] or '', "ban_reason": user[7] or '',
                        "phone": user[8] or '', "birth_date": user[9].isoformat() if user[9] else '',
                        "last_seen": user[10].isoformat() if user[10] else '',
                        "account_status": user[11] or 'active', "warning_info": warning_info
                    })
                return cors_response({"status": "error"})
    except Exception as e:
        print(f"User full info error: {e}")
        return cors_response({"status": "error"})

async def api_admin_get_user_chats(request):
    try:
        username = request.match_info['username']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT DISTINCT 
                        CASE 
                            WHEN from_user = %s THEN to_user 
                            ELSE from_user 
                        END as chat
                    FROM messages 
                    WHERE (from_user = %s OR to_user = %s) AND deleted = 0
                """, (username, username, username))
                chats = await cur.fetchall()
                
                result = []
                for chat in chats:
                    if chat[0] and chat[0] != username and chat[0] != 'System Support':
                        result.append(chat[0])
                return cors_response({"chats": result})
    except Exception as e:
        print(f"User chats error: {e}")
        return cors_response({"chats": []})

async def api_admin_get_chat_messages(request):
    try:
        username = request.match_info['username']
        chat_with = request.query.get('chat_with', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, to_user, message, timestamp 
                    FROM messages 
                    WHERE ((from_user=%s AND to_user=%s) OR (from_user=%s AND to_user=%s)) AND deleted=0
                    ORDER BY timestamp
                """, (username, chat_with, chat_with, username))
                messages = await cur.fetchall()
                return cors_response([{
                    "id": m[0], "from": m[1], "to": m[2], "message": m[3],
                    "timestamp": m[4].isoformat() if m[4] else ''
                } for m in messages])
    except Exception as e:
        print(f"Chat messages error: {e}")
        return cors_response([])

async def api_admin_set_role(request):
    try:
        data = await request.json()
        username = data.get('username')
        role = data.get('role')
        admin = data.get('admin', 'admin')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE users SET role=%s WHERE username=%s", (role, username))
                await add_log(admin, 'set_role', username, f'Роль изменена на {role}')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Set role error: {e}")
        return cors_response({"status": "error"})

async def api_admin_verify(request):
    try:
        data = await request.json()
        username = data.get('username')
        is_verified = data.get('is_verified', 0)
        admin = data.get('admin', 'admin')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE users SET is_verified=%s WHERE username=%s", (is_verified, username))
                await add_log(admin, 'verify_user', username, f'Верификация: {is_verified}')
                
                if username in active_connections:
                    try:
                        await active_connections[username].send_json({
                            "type": "verification_changed",
                            "is_verified": is_verified
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Verify error: {e}")
        return cors_response({"status": "error"})

async def api_admin_change_display_name(request):
    try:
        data = await request.json()
        username = data.get('username')
        new_display_name = data.get('new_display_name')
        admin = data.get('admin', 'admin')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE users SET display_name=%s WHERE username=%s", (new_display_name, username))
                await add_log(admin, 'change_display_name', username, f'Имя изменено на {new_display_name}')
                
                if username in active_connections:
                    try:
                        await active_connections[username].send_json({
                            "type": "profile_updated",
                            "display_name": new_display_name
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Change display name error: {e}")
        return cors_response({"status": "error"})

async def api_admin_update_user_field(request):
    try:
        data = await request.json()
        username = data.get('username')
        field = data.get('field')
        value = data.get('value')
        admin = data.get('admin', 'admin')
        
        allowed_fields = ['phone', 'birth_date']
        if field in allowed_fields:
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    if field == 'birth_date':
                        value = value if value else None
                    await cur.execute(f"UPDATE users SET {field}=%s WHERE username=%s", (value, username))
                    await add_log(admin, 'update_user_field', username, f'Поле {field} обновлено')
                    return cors_response({"status": "success"})
        return cors_response({"status": "error", "message": "Недопустимое поле"})
    except Exception as e:
        print(f"Update user field error: {e}")
        return cors_response({"status": "error"})

async def api_admin_issue_warning(request):
    try:
        data = await request.json()
        username = data.get('username')
        reason = data.get('reason')
        issued_by = data.get('issued_by')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO warnings (username, reason, issued_by, issued_at) 
                    VALUES (%s, %s, %s, %s)
                """, (username, reason, issued_by, datetime.now()))
                await cur.execute("""
                    UPDATE users SET warnings_count = warnings_count + 1, has_active_warning = 1 WHERE username=%s
                """, (username,))
                await add_log(issued_by, 'issue_warning', username, reason)
                await add_punishment(username, 'warning', reason, issued_by)
                
                warning_msg = f"""⚠️ Уважаемый пользователь! Администрация сервиса выдала Вам предупреждение по причине: {reason}. В случае повторного нарушения может быть применена более серьезная санкция (деактивация или удаление аккаунта). Ознакомьтесь с правилами Сообщества во избежании применения более серьезных санкций по отношению к Вашему аккаунту.

С уважением, администрация Enigma"""
                
                await cur.execute("""
                    INSERT INTO messages (from_user, to_user, message, timestamp, delivered) 
                    VALUES (%s, %s, %s, %s, %s)
                """, ('System Support', username, warning_msg, datetime.now(), 1))
                
                if username in active_connections:
                    try:
                        await active_connections[username].send_json({
                            "type": "new_message",
                            "from": "🛡️ Уведомления",
                            "text": warning_msg,
                            "timestamp": datetime.now().isoformat()
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Issue warning error: {e}")
        return cors_response({"status": "error"})

async def api_admin_resolve_warning(request):
    try:
        data = await request.json()
        username = data.get('username')
        resolved_by = data.get('resolved_by')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE users SET warnings_count = 0, has_active_warning = 0 WHERE username=%s
                """, (username,))
                await cur.execute("""
                    UPDATE warnings SET resolved=1, resolved_at=%s, resolved_by=%s 
                    WHERE username=%s AND resolved=0
                """, (datetime.now(), resolved_by, username))
                await add_log(resolved_by, 'resolve_warning', username, 'Снял предупреждение')
                await resolve_punishment(username, 'warning', resolved_by)
                
                resolve_msg = f"""✅ Уважаемый пользователь! Администрация сервиса сняла с Вас предупреждение. Надеемся на дальнейшее соблюдение правил сообщества.

С уважением, администрация Enigma"""
                
                await cur.execute("""
                    INSERT INTO messages (from_user, to_user, message, timestamp, delivered) 
                    VALUES (%s, %s, %s, %s, %s)
                """, ('System Support', username, resolve_msg, datetime.now(), 1))
                
                if username in active_connections:
                    try:
                        await active_connections[username].send_json({
                            "type": "new_message",
                            "from": "🛡️ Уведомления",
                            "text": resolve_msg,
                            "timestamp": datetime.now().isoformat()
                        })
                    except:
                        pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Resolve warning error: {e}")
        return cors_response({"status": "error"})

async def api_admin_manage_account(request):
    try:
        data = await request.json()
        username = data.get('username')
        action = data.get('action')
        reason = data.get('reason', '')
        admin = data.get('admin', 'admin')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                if action == 'deactivate':
                    await cur.execute("""
                        UPDATE users SET account_status='deactivated', ban_reason=%s WHERE username=%s
                    """, (reason, username))
                    await add_punishment(username, 'deactivation', reason, admin)
                    deactivate_msg = f"""⚠️ Ваш аккаунт временно отключен. Причина: {reason}. Если Вы не согласны с данным решением, Вы можете обратиться на электронную почту example@example.com с указанием username аккаунта, а также с Вашей версией произошедшего.

С уважением, администрация Enigma"""
                    await cur.execute("""
                        INSERT INTO messages (from_user, to_user, message, timestamp, delivered) 
                        VALUES (%s, %s, %s, %s, %s)
                    """, ('System Support', username, deactivate_msg, datetime.now(), 1))
                    
                    if username in active_connections:
                        try:
                            await active_connections[username].send_json({
                                "type": "account_deactivated",
                                "reason": reason,
                                "message": deactivate_msg
                            })
                        except:
                            pass
                elif action == 'delete':
                    await cur.execute("""
                        UPDATE users SET account_status='deleted', ban_reason=%s WHERE username=%s
                    """, (reason, username))
                    await add_punishment(username, 'deletion', reason, admin)
                    delete_date = datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
                    delete_msg = f"""⚠️ Уважаемый пользователь! Ваш аккаунт удален в связи с: {reason}. Если Вы не согласны с данным решением, Вы можете обратиться на электронную почту example@example.com с указанием username аккаунта, а также с Вашей версией произошедшего. В случае не поступления обратной связи в течение 30 дней после удаления, Ваш аккаунт будет удален без возможности восстановления.

Дата удаления: {delete_date}

С уважением, администрация Enigma"""
                    await cur.execute("""
                        INSERT INTO messages (from_user, to_user, message, timestamp, delivered) 
                        VALUES (%s, %s, %s, %s, %s)
                    """, ('System Support', username, delete_msg, datetime.now(), 1))
                    
                    if username in active_connections:
                        try:
                            await active_connections[username].send_json({
                                "type": "account_deleted",
                                "reason": reason,
                                "message": delete_msg
                            })
                        except:
                            pass
                await add_log(admin, f'{action}_account', username, reason)
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Manage account error: {e}")
        return cors_response({"status": "error"})

async def api_admin_restore_account(request):
    try:
        data = await request.json()
        username = data.get('username')
        admin = data.get('admin', '')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT role FROM users WHERE username=%s", (admin,))
                admin_role = await cur.fetchone()
                if not admin_role or admin_role[0] != 'owner':
                    return cors_response({"status": "error", "message": "Только владелец продукта может восстанавливать аккаунты"})
                
                await cur.execute("""
                    UPDATE users SET account_status='active', ban_reason='' WHERE username=%s
                """, (username,))
                await add_log(admin, 'restore_account', username, 'Восстановил аккаунт')
                
                restore_msg = f"""✅ Уважаемый пользователь! Администрация сервиса восстановила Ваш аккаунт. Надеемся на дальнейшее соблюдение правил сообщества. В случае повторного нарушения, аккаунт будет удален без возможности восстановления.

С уважением, администрация Enigma"""
                
                await cur.execute("""
                    INSERT INTO messages (from_user, to_user, message, timestamp, delivered) 
                    VALUES (%s, %s, %s, %s, %s)
                """, ('System Support', username, restore_msg, datetime.now(), 1))
                
                if username in active_connections:
                    try:
                        await active_connections[username].send_json({
                            "type": "account_restored",
                            "message": restore_msg
                        })
                    except:
                        pass
                return cors_response({"status": "success", "message": f"Аккаунт {username} восстановлен"})
    except Exception as e:
        print(f"Restore account error: {e}")
        return cors_response({"status": "error", "message": str(e)}, status=500)

async def api_admin_tickets(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, title, status, created_at, archived 
                    FROM tickets WHERE archived=0 ORDER BY created_at DESC
                """)
                tickets = await cur.fetchall()
                return cors_response([{
                    "id": t[0], "from_user": t[1], "title": t[2], "status": t[3],
                    "created_at": t[4].isoformat() if t[4] else '', "archived": t[5]
                } for t in tickets])
    except Exception as e:
        print(f"Admin tickets error: {e}")
        return cors_response([])

async def api_admin_ticket_reply(request):
    try:
        data = await request.json()
        ticket_id = data.get('ticket_id')
        reply = data.get('reply')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT messages, from_user, archived, status FROM tickets WHERE id=%s", (ticket_id,))
                ticket = await cur.fetchone()
                if ticket and ticket[2] != 1 and ticket[3] != 'closed':
                    messages_list = json.loads(ticket[0])
                    messages_list.append({"from": "System Support", "message": reply, "timestamp": datetime.now().isoformat()})
                    await cur.execute("""
                        UPDATE tickets SET messages=%s, status='replied', updated_at=%s WHERE id=%s
                    """, (json.dumps(messages_list), datetime.now(), ticket_id))
                    await add_log('admin', 'ticket_admin_reply', ticket_id, reply[:50])
                    
                    if ticket[1] in active_connections:
                        try:
                            await active_connections[ticket[1]].send_json({
                                "type": "ticket_reply",
                                "ticket_id": ticket_id
                            })
                        except:
                            pass
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Admin ticket reply error: {e}")
        return cors_response({"status": "error"})

async def api_admin_ticket_status(request):
    try:
        data = await request.json()
        ticket_id = data.get('ticket_id')
        status = data.get('status')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE tickets SET status=%s, updated_at=%s WHERE id=%s
                """, (status, datetime.now(), ticket_id))
                await add_log('admin', 'ticket_status_change', ticket_id, f'Статус изменён на {status}')
                
                if status == 'closed':
                    await cur.execute("UPDATE tickets SET archived=1 WHERE id=%s", (ticket_id,))
                    await cur.execute("SELECT from_user, messages FROM tickets WHERE id=%s", (ticket_id,))
                    ticket = await cur.fetchone()
                    if ticket and ticket[0] in active_connections:
                        messages = json.loads(ticket[1] or '[]')
                        has_support_reply = any(m.get('from') == 'System Support' for m in messages)
                        if has_support_reply:
                            await active_connections[ticket[0]].send_json({
                                "type": "show_rating",
                                "ticket_id": ticket_id
                            })
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Ticket status error: {e}")
        return cors_response({"status": "error"})

async def api_admin_bug_reports(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, bug_type, message, priority, status, timestamp 
                    FROM bug_reports ORDER BY timestamp DESC
                """)
                reports = await cur.fetchall()
                return cors_response([{
                    "id": r[0], "from_user": r[1], "bug_type": r[2], "message": r[3],
                    "priority": r[4], "status": r[5], "timestamp": r[6].isoformat() if r[6] else ''
                } for r in reports])
    except Exception as e:
        print(f"Bug reports error: {e}")
        return cors_response([])

async def api_admin_bug_status(request):
    try:
        data = await request.json()
        bug_id = data.get('bug_id')
        status = data.get('status')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE bug_reports SET status=%s WHERE id=%s", (status, bug_id))
                await add_log('admin', 'bug_status_change', str(bug_id), f'Статус изменён на {status}')
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Bug status error: {e}")
        return cors_response({"status": "error"})

async def api_admin_ratings(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT rating, comment, rated_by, timestamp FROM support_ratings ORDER BY timestamp DESC
                """)
                ratings = await cur.fetchall()
                return cors_response([{
                    "rating": r[0], "comment": r[1], "rated_by": r[2],
                    "timestamp": r[3].isoformat() if r[3] else ''
                } for r in ratings])
    except Exception as e:
        print(f"Ratings error: {e}")
        return cors_response([])

async def api_admin_logs(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT username, action, target, details, timestamp 
                    FROM logs ORDER BY timestamp DESC LIMIT 500
                """)
                logs = await cur.fetchall()
                return cors_response([{
                    "username": l[0], "action": l[1], "target": l[2],
                    "details": l[3], "timestamp": l[4].isoformat() if l[4] else ''
                } for l in logs])
    except Exception as e:
        print(f"Logs error: {e}")
        return cors_response([])

async def api_admin_get_complaints(request):
    try:
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, type, target, reason, details, status, timestamp 
                    FROM complaints ORDER BY timestamp DESC
                """)
                complaints = await cur.fetchall()
                return cors_response([{
                    "id": c[0], "from_user": c[1], "type": c[2], "target": c[3],
                    "reason": c[4], "details": c[5], "status": c[6],
                    "timestamp": c[7].isoformat() if c[7] else ''
                } for c in complaints])
    except Exception as e:
        print(f"Get complaints error: {e}")
        return cors_response([])

async def api_admin_resolve_complaint(request):
    try:
        data = await request.json()
        complaint_id = data.get('complaint_id')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE complaints SET status='resolved' WHERE id=%s", (complaint_id,))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Resolve complaint error: {e}")
        return cors_response({"status": "error"})

async def api_admin_delete_full_chat(request):
    try:
        data = await request.json()
        chat_id = data.get('chat_id')
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE messages SET deleted=1 WHERE to_user=%s OR from_user=%s", (chat_id, chat_id))
                return cors_response({"status": "success"})
    except Exception as e:
        print(f"Delete full chat error: {e}")
        return cors_response({"status": "error"})

# ========== ПУНКЦИИ НАКАЗАНИЙ ==========

async def api_get_punishments(request):
    try:
        username = request.match_info['username']
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT type, reason, issued_by, issued_at, resolved, resolved_at, resolved_by 
                    FROM punishments WHERE username=%s ORDER BY issued_at DESC
                """, (username,))
                punishments = await cur.fetchall()
                return cors_response([{
                    "type": p[0], "reason": p[1], "issued_by": p[2],
                    "issued_at": p[3].isoformat() if p[3] else '',
                    "resolved": p[4], "resolved_at": p[5].isoformat() if p[5] else '',
                    "resolved_by": p[6]
                } for p in punishments])
    except Exception as e:
        print(f"Get punishments error: {e}")
        return cors_response([])

# ========== WEBSOCKET ==========

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    username = request.query.get('username')
    
    if username:
        active_connections[username] = ws
        print(f"WebSocket connected: {username}")
        
        async with db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id, from_user, message, file_type, file_data, timestamp 
                    FROM messages 
                    WHERE to_user=%s AND read_status=0 AND deleted=0
                """, (username,))
                unread = await cur.fetchall()
                for msg in unread:
                    try:
                        await ws.send_json({
                            "type": "new_message",
                            "id": msg[0],
                            "from": msg[1],
                            "text": msg[2],
                            "file_type": msg[3] or '',
                            "file_data": msg[4] or '',
                            "timestamp": msg[5].isoformat() if msg[5] else ''
                        })
                    except:
                        pass
    
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get('type') == 'mark_read':
                    await api_mark_read(data)
                elif data.get('type') == 'mark_delivered':
                    await api_mark_delivered(data)
    except:
        pass
    finally:
        if username in active_connections:
            del active_connections[username]
            print(f"WebSocket disconnected: {username}")
    
    return ws

# ========== ЗАПУСК ==========

app = web.Application()
app.router.add_options('/{path:.*}', options_handler)

# Пользовательские API
app.router.add_post('/api/login', api_login)
app.router.add_post('/api/register', api_register)
app.router.add_post('/api/change_password', api_change_password)
app.router.add_post('/api/profile', api_update_profile)
app.router.add_post('/api/update_avatar', api_update_avatar)
app.router.add_get('/api/contacts/{username}', api_get_contacts)
app.router.add_get('/api/search_users', api_search_users)
app.router.add_get('/api/get_messages/{chat_id}', api_get_messages)
app.router.add_post('/api/send', api_send)
app.router.add_post('/api/send_group', api_send_group)
app.router.add_post('/api/edit_message', api_edit_message)
app.router.add_post('/api/delete_message', api_delete_message)
app.router.add_post('/api/mark_delivered', api_mark_delivered)
app.router.add_post('/api/mark_read', api_mark_read)
app.router.add_post('/api/mark_chat_read', api_mark_chat_read)
app.router.add_get('/api/groups', api_get_groups)
app.router.add_post('/api/group/create', api_create_group)
app.router.add_post('/api/group/rename', api_rename_group)
app.router.add_post('/api/group/add_member', api_add_group_member)
app.router.add_post('/api/group/remove_member', api_remove_group_member)
app.router.add_post('/api/group/leave/{group_id}', api_leave_group)
app.router.add_get('/api/group_info/{group_id}', api_get_group_info)
app.router.add_get('/api/get_chat_stats/{chat_id}', api_get_chat_stats)
app.router.add_get('/api/get_chat_stat_counts/{chat_id}', api_get_chat_stat_counts)
app.router.add_post('/api/add_to_favorites', api_add_to_favorites)
app.router.add_get('/api/user_status/{username}', api_get_user_status)
app.router.add_get('/api/user_full_info/{username}', api_get_user_full_info)
app.router.add_post('/api/block_user', api_block_user)
app.router.add_post('/api/unblock_user', api_unblock_user)
app.router.add_get('/api/is_blocked/{username}', api_is_blocked)
app.router.add_post('/api/send_notification', api_send_notification)
app.router.add_post('/api/complaint/create', api_create_complaint)
app.router.add_post('/api/ticket/create', api_create_ticket)
app.router.add_get('/api/tickets/{username}', api_get_user_tickets)
app.router.add_get('/api/ticket/{ticket_id}', api_get_ticket)
app.router.add_post('/api/ticket/reply', api_ticket_reply)
app.router.add_post('/api/bug_report', api_bug_report)
app.router.add_post('/api/rate_support', api_rate_support)
app.router.add_get('/api/punishments/{username}', api_get_punishments)

# Админ API
app.router.add_get('/api/admin/users', api_admin_users)
app.router.add_get('/api/admin/user_full_info/{username}', api_admin_user_full_info)
app.router.add_get('/api/admin/user_chats/{username}', api_admin_get_user_chats)
app.router.add_get('/api/admin/chat_messages/{username}', api_admin_get_chat_messages)
app.router.add_post('/api/admin/set_role', api_admin_set_role)
app.router.add_post('/api/admin/verify', api_admin_verify)
app.router.add_post('/api/admin/change_display_name', api_admin_change_display_name)
app.router.add_post('/api/admin/update_user_field', api_admin_update_user_field)
app.router.add_post('/api/admin/issue_warning', api_admin_issue_warning)
app.router.add_post('/api/admin/resolve_warning', api_admin_resolve_warning)
app.router.add_post('/api/admin/manage_account', api_admin_manage_account)
app.router.add_post('/api/admin/restore_account', api_admin_restore_account)
app.router.add_get('/api/admin/tickets', api_admin_tickets)
app.router.add_post('/api/admin/ticket_reply', api_admin_ticket_reply)
app.router.add_post('/api/admin/ticket_status', api_admin_ticket_status)
app.router.add_get('/api/admin/bug_reports', api_admin_bug_reports)
app.router.add_post('/api/admin/bug_status', api_admin_bug_status)
app.router.add_get('/api/admin/ratings', api_admin_ratings)
app.router.add_get('/api/admin/logs', api_admin_logs)
app.router.add_get('/api/admin/complaints', api_admin_get_complaints)
app.router.add_post('/api/admin/resolve_complaint', api_admin_resolve_complaint)
app.router.add_post('/api/admin/delete_full_chat', api_admin_delete_full_chat)

app.router.add_get('/ws', websocket_handler)

async def on_startup(app):
    await init_db_pool()
    print("=" * 50)
    print("🚀 Enigma Messenger Backend (MySQL)")
    print("=" * 50)
    print("🌐 HTTP API: http://0.0.0.0:8080")
    print("🔌 WebSocket: ws://0.0.0.0:8080/ws")
    print("=" * 50)

app.on_startup.append(on_startup)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8080)
