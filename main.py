import re
import requests
import json
from urllib.parse import quote
import pandas as pd
import hashlib
import urllib
import time
import datetime
import threading
import sqlite3

# --- 全局变量和数据库设置 ---
DB_FILE = "comment_monitor.db"
video_states = {}
manual_update_event = threading.Event()
stop_event = threading.Event()


# --- 数据库核心功能 (已升级) ---

def init_db():
    """初始化数据库，创建表并永久启用 WAL 模式以支持高并发"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 关键改动：为数据库文件启用 WAL (Write-Ahead Logging) 模式。
    # 这是一个持久化设置，只需执行一次。它能极大地提升并发写入性能。
    cursor.execute("PRAGMA journal_mode=WAL;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_cache (
            oid TEXT NOT NULL,
            rpid TEXT NOT NULL,
            reply_count INTEGER NOT NULL,
            PRIMARY KEY (oid, rpid)
        )
    ''')
    conn.commit()
    conn.close()


def load_data_from_db():
    """从数据库加载所有视频的历史数据"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if not video_states:
        conn.close()
        return

    oids_to_load = tuple(video_states.keys())
    query = f"SELECT oid, rpid, reply_count FROM comment_cache WHERE oid IN ({','.join(['?'] * len(oids_to_load))})"

    try:
        cursor.execute(query, oids_to_load)
        count = 0
        for row in cursor.fetchall():
            oid, rpid, reply_count = row
            if oid in video_states:
                video_states[oid]['seen_ids'].add(rpid)
                if reply_count >= 0:
                    video_states[oid]['rcounts'][rpid] = reply_count
                count += 1
        print(f"成功从数据库为当前监控的视频加载了 {count} 条历史评论记录。")
    except Exception as e:
        print(f"从数据库加载数据时出错: {e}")
    finally:
        conn.close()


# --- Bilibili API 相关函数 (保持不变) ---
def get_Header():
    try:
        with open('bili_cookie.txt', 'r') as f:
            cookie = f.read()
    except FileNotFoundError:
        print("错误：找不到 'bili_cookie.txt'。");
        return None
    return {"Cookie": cookie, "User-Agent": 'Mozilla/5.0'}


def get_information(bv, header):
    url = f"https://www.bilibili.com/video/{bv}/"
    try:
        resp = requests.get(url, headers=header);
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"获取影片 {bv} 信息时出错：{e}");
        return None, None
    oid_match = re.search(f'"aid":(?P<id>\d+),"bvid":"{bv}"', resp.text)
    if not oid_match: print(f"找不到影片 {bv} 的 oid (aid)。"); return None, None
    oid = oid_match.group('id')
    title_match = re.search(r'<title data-vue-meta="true">(.*?)_哔哩哔哩_bilibili</title>', resp.text)
    title = title_match.group(1).strip() if title_match else f"未知标题 (BV:{bv})"
    return oid, title


def md5(code):
    MD5 = hashlib.md5();
    MD5.update(code.encode('utf-8'));
    return MD5.hexdigest()


def fetch_latest_comments(oid, header):
    if not oid: return []
    params = {'oid': oid, 'type': 1, 'mode': 2, 'plat': 1, 'web_location': 1315875, 'wts': int(time.time())}
    mixin_key_salt = "ea1db124af3c7062474693fa704f4ff8"
    query_for_w_rid = urllib.parse.urlencode(sorted(params.items())) + mixin_key_salt
    params['w_rid'] = md5(query_for_w_rid)
    url = f"https://api.bilibili.com/x/v2/reply/wbi/main?{urllib.parse.urlencode(params)}"
    try:
        response = requests.get(url, headers=header);
        response.raise_for_status()
        return response.json().get('data', {}).get('replies', []) or []
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"获取 OID:{oid} 的主评论时出错：{e}");
        return []


def fetch_sub_comments(oid, root_rpid, header):
    url = "https://api.bilibili.com/x/v2/reply/reply"
    params = {'oid': oid, 'type': 1, 'root': root_rpid, 'ps': 20, 'pn': 1, 'web_location': 333.788}
    try:
        response = requests.get(url, params=params, headers=header);
        response.raise_for_status()
        return response.json().get('data', {}).get('replies', []) or []
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"    [!] 获取子评论时出错 (root: {root_rpid}): {e}");
        return []


# --- 核心处理逻辑 (保持不变) ---
def process_and_display_comments(oid, title, header, db_conn):
    latest_comments = fetch_latest_comments(oid, header)
    if not latest_comments: print(f"[{title}] 找不到评论或获取失败。"); return

    video_state = video_states[oid]
    seen_ids = video_state['seen_ids']
    rcounts = video_state['rcounts']

    new_items_found_this_cycle = False
    cursor = db_conn.cursor()

    for comment in latest_comments:
        rpid = comment['rpid_str']
        current_rcount = comment.get('rcount', 0)
        user_name = comment['member']['uname']

        if rpid not in seen_ids:
            new_items_found_this_cycle = True
            print(
                "\n" + "=" * 40 + f"\n[{title}] >>> 发现新主评论！ (来自: {user_name})\n" + f"  内容: {comment['content']['message']}\n" + "=" * 40)
            seen_ids.add(rpid);
            rcounts[rpid] = current_rcount
            cursor.execute("INSERT OR IGNORE INTO comment_cache (oid, rpid, reply_count) VALUES (?, ?, ?)",
                           (oid, rpid, current_rcount))

            if current_rcount > 0:
                time.sleep(0.5);
                sub_comments = fetch_sub_comments(oid, rpid, header)
                if sub_comments:
                    for sub in sub_comments:
                        sub_rpid = sub['rpid_str'];
                        seen_ids.add(sub_rpid)
                        cursor.execute("INSERT OR IGNORE INTO comment_cache (oid, rpid, reply_count) VALUES (?, ?, 0)",
                                       (oid, sub_rpid,))
                        print(f"    └── 回复: {sub['member']['uname']} - {sub['content']['message']}")
        else:
            old_rcount = rcounts.get(rpid, 0)
            if current_rcount > old_rcount:
                new_items_found_this_cycle = True
                print("\n" + "*" * 40 + f"\n[{title}] >>> 检测到新的回复！ (在 {user_name} 的评论下)\n" + "*" * 40)
                rcounts[rpid] = current_rcount
                cursor.execute("UPDATE comment_cache SET reply_count = ? WHERE oid = ? AND rpid = ?",
                               (current_rcount, oid, rpid))

                time.sleep(0.5);
                sub_comments = fetch_sub_comments(oid, rpid, header)
                if sub_comments:
                    for sub in sub_comments:
                        sub_rpid = sub['rpid_str']
                        if sub_rpid not in seen_ids:
                            seen_ids.add(sub_rpid)
                            cursor.execute(
                                "INSERT OR IGNORE INTO comment_cache (oid, rpid, reply_count) VALUES (?, ?, 0)",
                                (oid, sub_rpid,))
                            print(f"    └── 新回复: {sub['member']['uname']} - {sub['content']['message']}")

    db_conn.commit()
    if not new_items_found_this_cycle:
        print(f"[{title}] 本次更新无新内容。")


# --- 后台线程工作函数 (已升级) ---
def run_monitor_worker(oid, title, header, interval_seconds):
    db_conn = None
    try:
        # 关键改动：增加 timeout 参数。如果数据库被锁，此连接会等待最多15秒，而不是立即报错。
        db_conn = sqlite3.connect(DB_FILE, timeout=15.0)
        print(f"[线程 {threading.get_ident()} 已启动] 负责监控: {title}")

        while not stop_event.is_set():
            triggered_manually = manual_update_event.wait(timeout=interval_seconds)
            if stop_event.is_set(): break

            now = datetime.datetime.now().strftime('%H:%M:%S')
            if manual_update_event.is_set():  # 检查是否是手动触发
                print(f"\n[{now}] [手动更新] 正在检查: {title}")
            else:
                print(f"\n[{now}] [自动更新] 正在检查: {title}")

            process_and_display_comments(oid, title, header, db_conn)

        # 在手动触发后，清除事件，以免影响其他线程的判断
        if manual_update_event.is_set():
            manual_update_event.clear()

    except Exception as e:
        # 捕获并打印线程内的任何其他异常
        print(f"\n[错误] 监控 '{title}' 的线程 ({threading.get_ident()}) 遇到致命错误: {e}")
    finally:
        if db_conn:
            db_conn.close()
            print(f"\n[线程 {threading.get_ident()} 已结束] 停止监控: {title}")


# --- 主程序入口 (已升级) ---
if __name__ == "__main__":
    header = get_Header()
    if not header: exit()

    init_db()

    bv_inputs = input("请输入一个或多个影片 BV 号，用英文逗号 ',' 隔开：\n").strip()
    bv_ids = [bv.strip() for bv in bv_inputs.split(',') if bv.strip()]

    if not bv_ids:
        print("没有输入有效的 BV 号，程序退出。");
        exit()

    print("\n正在获取视频信息...")
    for bv_id in bv_ids:
        oid, title = get_information(bv_id, header)
        if oid and title:
            if oid not in video_states:
                video_states[oid] = {"title": title, "seen_ids": set(), "rcounts": {}}
                print(f"  - [{title}] (oid: {oid}) 将加入监控列表。")

    if not video_states:
        print("未能获取任何有效视频信息，程序退出。");
        exit()

    load_data_from_db()

    while True:
        try:
            interval_min = float(input("\n请输入自动更新的间隔时间（分钟）："))
            if interval_min > 0:
                interval_sec = interval_min * 60; break
            else:
                print("时间必须是正数。")
        except ValueError:
            print("输入无效，请输入一个数字。")

    threads = []
    print("\n--- 开始监控 ---")
    for oid, state in video_states.items():
        thread = threading.Thread(target=run_monitor_worker, args=(oid, state['title'], header, interval_sec))
        threads.append(thread);
        thread.start()
        time.sleep(0.1)

    print("\n所有监控线程已启动。")
    print("按 Enter 手动更新所有视频，或输入 'exit' 退出程序。")
    manual_update_event.set()

    try:
        while any(t.is_alive() for t in threads):
            command = input()
            if command.lower() == 'exit':
                print("正在准备退出所有监控线程...");
                stop_event.set();
                manual_update_event.set();
                break
            else:
                manual_update_event.set()
    finally:
        for t in threads:
            t.join()
        print("\n程序已成功退出。")

