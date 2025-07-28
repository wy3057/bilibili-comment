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

# 这些变量将由数据库在启动时填充
seen_comment_ids = set()
main_comment_rcounts = {}

# 线程通信对象
manual_update_event = threading.Event()
stop_event = threading.Event()


# --- 数据库核心功能 ---

def init_db():
    """初始化数据库，创建表（如果不存在）。此函数只在主线程中调用一次。"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comment_cache (
            rpid TEXT PRIMARY KEY,
            reply_count INTEGER NOT NULL
        )
    ''')
    conn.commit()
    conn.close()  # 完成初始化后立即关闭连接


def load_data_from_db():
    """从数据库加载数据到内存变量中。此函数也在主线程中调用。"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT rpid, reply_count FROM comment_cache")
    count = 0
    for row in cursor.fetchall():
        rpid, reply_count = row
        seen_comment_ids.add(rpid)
        if reply_count >= 0:  # 主评论和子评论都加载
            main_comment_rcounts[rpid] = reply_count
        count += 1
    conn.close()
    print(f"成功从数据库加载了 {count} 条历史评论记录。")


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
        print(f"获取影片信息时出错：{e}");
        return None, None
    oid_match = re.search(f'"aid":(?P<id>\d+),"bvid":"{bv}"', resp.text)
    if not oid_match: print("找不到影片 oid (aid)。"); return None, None
    oid = oid_match.group('id')
    title_match = re.search(r'<title data-vue-meta="true">(.*?)_哔哩哔哩_bilibili</title>', resp.text)
    title = title_match.group(1) if title_match else "未知标题"
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
        print(f"获取主评论时出错：{e}");
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


# --- 核心处理逻辑 (保持不变, 但接收的db_conn是线程自己的) ---
def process_and_display_comments(oid, header, db_conn):
    latest_comments = fetch_latest_comments(oid, header)
    if not latest_comments: print("找不到评论或获取失败。"); return

    new_items_found_this_cycle = False
    cursor = db_conn.cursor()

    for comment in latest_comments:
        rpid = comment['rpid_str']
        current_rcount = comment.get('rcount', 0)
        user_name = comment['member']['uname']

        if rpid not in seen_comment_ids:
            new_items_found_this_cycle = True
            print(
                "\n" + "=" * 40 + f"\n>>> 发现新主评论！ (来自: {user_name})\n" + f"  内容: {comment['content']['message']}\n" + f"  时间: {pd.to_datetime(comment['ctime'], unit='s')}\n" + "=" * 40)
            seen_comment_ids.add(rpid)
            main_comment_rcounts[rpid] = current_rcount
            cursor.execute("INSERT OR IGNORE INTO comment_cache (rpid, reply_count) VALUES (?, ?)",
                           (rpid, current_rcount))

            if current_rcount > 0:
                time.sleep(0.5);
                sub_comments = fetch_sub_comments(oid, rpid, header)
                if sub_comments:
                    print("  " + "-" * 25)
                    for sub in sub_comments:
                        sub_rpid = sub['rpid_str']
                        seen_comment_ids.add(sub_rpid)
                        cursor.execute("INSERT OR IGNORE INTO comment_cache (rpid, reply_count) VALUES (?, 0)",
                                       (sub_rpid,))
                        print(
                            f"    └── 回复者: {sub['member']['uname']}\n        内容: {sub['content']['message']}\n" + " " * 8 + "-" * 15)
        else:
            old_rcount = main_comment_rcounts.get(rpid, 0)
            if current_rcount > old_rcount:
                new_items_found_this_cycle = True
                print("\n" + "*" * 40 + f"\n>>> 检测到新的回复！ (在 {user_name} 的评论下)\n" + "*" * 40)
                main_comment_rcounts[rpid] = current_rcount
                cursor.execute("UPDATE comment_cache SET reply_count = ? WHERE rpid = ?", (current_rcount, rpid))

                time.sleep(0.5);
                sub_comments = fetch_sub_comments(oid, rpid, header)
                if sub_comments:
                    for sub in sub_comments:
                        sub_rpid = sub['rpid_str']
                        if sub_rpid not in seen_comment_ids:
                            seen_comment_ids.add(sub_rpid)
                            cursor.execute("INSERT OR IGNORE INTO comment_cache (rpid, reply_count) VALUES (?, 0)",
                                           (sub_rpid,))
                            print(
                                f"    └── 新回复来自: {sub['member']['uname']}\n        内容: {sub['content']['message']}\n" + " " * 8 + "-" * 15)

    db_conn.commit()
    if not new_items_found_this_cycle:
        print("此次更新中没有发现新评论或新回复。")


# --- 修改后的后台线程工作函数 ---
def run_monitor_worker(oid, header, interval_seconds):
    """后台监控线程，它会自己创建和关闭数据库连接。"""
    db_conn = None  # 初始化变量
    try:
        # 在线程内部创建数据库连接
        db_conn = sqlite3.connect(DB_FILE)
        print(f"[线程 {threading.get_ident()}] 已成功连接到数据库。")

        while not stop_event.is_set():
            triggered_manually = manual_update_event.wait(timeout=interval_seconds)
            if stop_event.is_set(): break

            if triggered_manually:
                print("\n[手动更新触发！]")
                manual_update_event.clear()
            else:
                print(f"\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [自动更新] ...")

            # 将本线程自己的连接传入处理函数
            process_and_display_comments(oid, header, db_conn)

            if not stop_event.is_set():
                next_run_time = datetime.datetime.now() + datetime.timedelta(seconds=interval_seconds)
                print(f"\n下次自动更新: {next_run_time.strftime('%H:%M:%S')}。按 Enter 手动更新, 或输入 'exit' 退出：",
                      end="", flush=True)
    finally:
        # 使用 finally 确保无论如何线程退出时都会关闭连接
        if db_conn:
            db_conn.close()
            print(f"\n[线程 {threading.get_ident()}] 数据库连接已关闭。")


if __name__ == "__main__":
    header = get_Header()
    if not header: exit()

    # --- 数据库初始化和加载 (在主线程中完成) ---
    init_db()
    load_data_from_db()

    bv_id = input("请输入影片 BV 号：")
    oid, title = get_information(bv_id, header)
    if not oid: exit()

    while True:
        try:
            interval_min = float(input("请输入自动更新的间隔时间（分钟）："))
            if interval_min > 0:
                interval_sec = interval_min * 60; break
            else:
                print("时间必须是正数。")
        except ValueError:
            print("输入无效，请输入一个数字。")

    print(f"\n成功！开始监控影片：{title} (oid: {oid})")

    # 创建线程时，不再传递数据库连接对象
    monitor_thread = threading.Thread(target=run_monitor_worker, args=(oid, header, interval_sec))
    monitor_thread.start()

    print("正在执行首次评论获取...")
    manual_update_event.set()

    try:
        while monitor_thread.is_alive():
            command = input()
            if command.lower() == 'exit':
                print("正在准备退出...");
                stop_event.set();
                manual_update_event.set();
                break
            else:
                manual_update_event.set()
    finally:
        monitor_thread.join()
        print("程序已成功退出。")
