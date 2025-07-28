# filename: database.py
import sqlite3
import datetime

DB_NAME = 'bilibili_monitor.db'

def init_db():
    """初始化數據庫，創建所需的表格（如果它們不存在的話）。"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # 創建影片表格
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            oid TEXT PRIMARY KEY,
            bv_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        # 創建已見評論表格
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_comments (
            rpid TEXT PRIMARY KEY,
            oid TEXT NOT NULL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (oid) REFERENCES videos (oid) ON DELETE CASCADE
        )
        ''')
        # 為 oid 創建索引以加速查詢
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_oid ON seen_comments (oid)')
        conn.commit()

def get_monitored_videos():
    """從數據庫獲取所有正在監控的影片列表。"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT oid, bv_id, title FROM videos ORDER BY added_at DESC')
        return cursor.fetchall()

def add_video_to_db(oid, bv_id, title):
    """將一個新影片添加到數據庫。"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO videos (oid, bv_id, title) VALUES (?, ?, ?)', (oid, bv_id, title))
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        print(f"提示：影片 {bv_id} ({title}) 已經在數據庫中。")
        return False

def remove_video_from_db(oid):
    """從數據庫中移除一個影片及其所有相關的已見評論。"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM videos WHERE oid = ?', (oid,))
        conn.commit()
        return cursor.rowcount > 0

def load_seen_comments_for_video(oid):
    """為給定的影片加載所有已見評論的 rpid 到一個集合中。"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT rpid FROM seen_comments WHERE oid = ?', (oid,))
        return {row[0] for row in cursor.fetchall()}

def add_comment_to_db(rpid, oid):
    """將一個新的已見評論 rpid 添加到數據庫。"""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO seen_comments (rpid, oid) VALUES (?, ?)', (rpid, oid))
        conn.commit()

