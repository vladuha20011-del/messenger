import sqlite3

DB_PATH = 'messenger.db'
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Список колонок, которые нужно проверить и добавить
columns_to_add = {
    'phone': "TEXT DEFAULT ''",
    'birth_date': "TEXT DEFAULT ''",
    'last_seen': "TEXT DEFAULT ''",
    'role': "TEXT DEFAULT 'user'",
    'is_verified': "INTEGER DEFAULT 0",
    'account_status': "TEXT DEFAULT 'active'",
    'ban_reason': "TEXT DEFAULT ''",
    'warnings_count': "INTEGER DEFAULT 0",
    'has_active_warning': "INTEGER DEFAULT 0"
}

# Получаем существующие колонки
c.execute("PRAGMA table_info(users)")
existing_columns = [row[1] for row in c.fetchall()]

# Добавляем недостающие
for col_name, col_type in columns_to_add.items():
    if col_name not in existing_columns:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            print(f"✅ Добавлена колонка {col_name}")
        except Exception as e:
            print(f"Ошибка при добавлении {col_name}: {e}")

# Проверяем, есть ли пользователь arm, если нет — создаём
import hashlib
c.execute("SELECT COUNT(*) FROM users WHERE username='arm'")
if c.fetchone()[0] == 0:
    password_hash = hashlib.sha256("123".encode()).hexdigest()
    c.execute("""INSERT INTO users (username, display_name, password_hash, role, is_verified, created_at, account_status) 
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              ('arm', 'arm', password_hash, 'owner', 1, '2024-01-01T00:00:00', 'active'))
    print("✅ Создан пользователь arm с паролем 123")
else:
    print("✅ Пользователь arm уже существует")

conn.commit()
conn.close()
print("\n✅ База данных готова!")