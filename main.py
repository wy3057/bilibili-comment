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

# --- 全局变量和线程通信对象 ---

# 用于存储所有已见评论 ID 的集合，防止重复回报
seen_comment_ids = set()
# 用于存储主评论ID及其回复数，以检测新的子评论
main_comment_rcounts = {}

# 线程通信对象
manual_update_event = threading.Event()
stop_event = threading.Event()


# --- 核心功能函数 ---

def get_Header():
    """
    从 'bili_cookie.txt' 读取 cookie 并构建请求头。
    """
    try:
        with open('bili_cookie.txt', 'r') as f:
            cookie = f.read()
    except FileNotFoundError:
        print("错误：找不到 'bili_cookie.txt'。请创建此文件并将您的 Bilibili cookie 粘贴到其中。")
        return None
    return {
        "Cookie": cookie,
        "User-Agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0'
    }


def get_information(bv, header):
    """
    抓取视频页面以提取 'oid' (即 'aid') 和视频标题。
    """
    url = f"https://www.bilibili.com/video/{bv}/"
    try:
        resp = requests.get(url, headers=header)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"获取影片信息时出错：{e}")
        return None, None
    oid_match = re.search(f'"aid":(?P<id>\d+),"bvid":"{bv}"', resp.text)
    if not oid_match:
        print("找不到影片 oid (aid)。影片可能不存在或页面结构已变更。")
        return None, None
    oid = oid_match.group('id')
    title_match = re.search(r'<title data-vue-meta="true">(.*?)_哔哩哔哩_bilibili</title>', resp.text)
    title = title_match.group(1) if title_match else "未知标题"
    return oid, title


def md5(code):
    """
    对输入字符串执行 MD5 哈希。
    """
    MD5 = hashlib.md5()
    MD5.update(code.encode('utf-8'))
    return MD5.hexdigest()


def fetch_latest_comments(oid, header):
    """
    抓取给定影片 oid 的第一页最新主评论。
    """
    if not oid: return []
    mode = 2
    params = {
        'oid': oid, 'type': 1, 'mode': mode, 'plat': 1,
        'web_location': 1315875, 'wts': int(time.time())
    }
    mixin_key_salt = "ea1db124af3c7062474693fa704f4ff8"
    query_for_w_rid = urllib.parse.urlencode(sorted(params.items())) + mixin_key_salt
    params['w_rid'] = md5(query_for_w_rid)
    url = f"https://api.bilibili.com/x/v2/reply/wbi/main?{urllib.parse.urlencode(params)}"
    try:
        response = requests.get(url, headers=header)
        response.raise_for_status()
        return response.json().get('data', {}).get('replies', []) or []
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"获取主评论时出错：{e}")
    return []


def fetch_sub_comments(oid, root_rpid, header):
    """
    抓取指定主评论 (root_rpid) 下的子评论。
    """
    url = "https://api.bilibili.com/x/v2/reply/reply"
    params = {'oid': oid, 'type': 1, 'root': root_rpid, 'ps': 20, 'pn': 1, 'web_location': 333.788}
    try:
        response = requests.get(url, params=params, headers=header)
        response.raise_for_status()
        return response.json().get('data', {}).get('replies', []) or []
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"    [!] 获取子评论时出错 (root: {root_rpid}): {e}")
    return []


# --- 更新后的执行与监控逻辑 ---

def process_and_display_comments(oid, header):
    """单次执行评论的获取、处理和显示，现在能检测新的子评论。"""
    latest_comments = fetch_latest_comments(oid, header)
    if not latest_comments:
        print("找不到评论或获取失败。")
        return

    new_items_found_this_cycle = False

    for comment in latest_comments:
        rpid = comment['rpid_str']
        current_rcount = comment.get('rcount', 0)
        user_name = comment['member']['uname']

        # Case 1: 发现一则全新的主评论
        if rpid not in seen_comment_ids:
            new_items_found_this_cycle = True
            seen_comment_ids.add(rpid)
            main_comment_rcounts[rpid] = current_rcount

            print("\n" + "=" * 40)
            print(f">>> 发现新主评论！ (来自: {user_name})")
            print(f"  内容: {comment['content']['message']}")
            print(f"  时间: {pd.to_datetime(comment['ctime'], unit='s')}")
            print("=" * 40)

            if current_rcount > 0:
                print(f"  -> 正在获取其 {current_rcount} 则回复...")
                time.sleep(0.5)
                sub_comments = fetch_sub_comments(oid, rpid, header)
                if sub_comments:
                    print("  " + "-" * 25)
                    for sub in sub_comments:
                        sub_rpid = sub['rpid_str']
                        seen_comment_ids.add(sub_rpid)  # 将所有子评论ID加入已见集合
                        print(f"    └── 回复者: {sub['member']['uname']}")
                        print(f"        内容: {sub['content']['message']}")
                        print(" " * 8 + "-" * 15)

        # Case 2: 已存在的主评论，检查是否有新回复
        else:
            old_rcount = main_comment_rcounts.get(rpid, 0)
            if current_rcount > old_rcount:
                new_items_found_this_cycle = True
                print("\n" + "*" * 40)
                print(f">>> 检测到新的回复！ (在 {user_name} 的评论下)")
                print("*" * 40)

                time.sleep(0.5)
                sub_comments = fetch_sub_comments(oid, rpid, header)

                new_sub_comments_found = 0
                if sub_comments:
                    for sub in sub_comments:
                        sub_rpid = sub['rpid_str']
                        if sub_rpid not in seen_comment_ids:
                            new_sub_comments_found += 1
                            seen_comment_ids.add(sub_rpid)
                            print(f"    └── 新回复来自: {sub['member']['uname']}")
                            print(f"        内容: {sub['content']['message']}")
                            print(f"        时间: {pd.to_datetime(sub['ctime'], unit='s')}")
                            print(" " * 8 + "-" * 15)

                if new_sub_comments_found == 0:
                    print("    (回复数已更新，但未能在第一页找到新回复，可能在旧的回复页)。")

                # 更新存储的回复数
                main_comment_rcounts[rpid] = current_rcount

    if not new_items_found_this_cycle:
        print("此次更新中没有发现新评论或新回复。")


def run_monitor_worker(oid, header, interval_seconds):
    """在后台线程中运行的监控循环。"""
    while not stop_event.is_set():
        triggered_manually = manual_update_event.wait(timeout=interval_seconds)
        if stop_event.is_set(): break

        if triggered_manually:
            print("\n[手动更新触发！]")
            manual_update_event.clear()
        else:
            print(f"\n[{datetime.datetime.now()}] [自动更新] 正在获取最新评论...")

        process_and_display_comments(oid, header)

        if not stop_event.is_set():
            next_run_time = datetime.datetime.now() + datetime.timedelta(seconds=interval_seconds)
            print(f"\n下一次自动更新将在 {next_run_time.strftime('%H:%M:%S')} 左右。")
            print("按 Enter 手动更新，或输入 'exit' 退出：", end="", flush=True)


if __name__ == "__main__":
    header = get_Header()
    if not header: exit()

    bv_id = input("请输入影片 BV 号：")
    oid, title = get_information(bv_id, header)
    if not oid: exit()

    while True:
        try:
            interval_min = float(input("请输入自动更新的间隔时间（分钟，例如 5）："))
            if interval_min > 0:
                interval_sec = interval_min * 60
                break
            else:
                print("时间必须是正数。")
        except ValueError:
            print("输入无效，请输入一个数字。")

    print(f"\n成功！开始监控影片：{title} (oid: {oid})")
    print(f"将每 {interval_min} 分钟自动更新一次。")

    monitor_thread = threading.Thread(target=run_monitor_worker, args=(oid, header, interval_sec))
    monitor_thread.start()

    print("正在执行首次评论获取...")
    manual_update_event.set()

    while True:
        command = input()
        if command.lower() == 'exit':
            print("正在准备退出...")
            stop_event.set()
            manual_update_event.set()
            break
        else:
            manual_update_event.set()

    monitor_thread.join()
    print("程序已成功退出。")
