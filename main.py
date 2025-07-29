# filename: monitor.py
import re
import sys
import requests
import json
import hashlib
import urllib.parse
import time
import datetime
import pandas as pd
import subprocess
import platform  # 导入 platform 模块来判断操作系统

# 根据操作系统导入不同的模块
if platform.system() == "Windows":
    import msvcrt
else:
    import select

# 导入我们自己的模块
import database as db
import notifier  # <-- 新增：导入通知模块


# --- 核心功能函数 ---

def get_header():
    """从 'bili_cookie.txt' 读取 cookie 并构建请求头。"""
    try:
        with open('bili_cookie.txt', 'r', encoding='utf-8') as f:
            cookie = f.read().strip()
        if not cookie:
            raise FileNotFoundError("Cookie 文件为空。")
    except FileNotFoundError:
        print("提示：'bili_cookie.txt' 文件未找到或为空。")
        print("正在尝试调用 'login_bilibili.py' 进行自动登录...")
        try:
            subprocess.run(
                [sys.executable, 'login_bilibili.py'],
                check=False,
                encoding='utf-8'
            )
            print("登录脚本执行完毕，将重新读取 Cookie。")
            with open('bili_cookie.txt', 'r', encoding='utf-8') as f:
                cookie = f.read().strip()
            if not cookie:
                print("错误：登录后 'bili_cookie.txt' 仍然为空，请手动检查登录过程是否成功。")
                sys.exit(1)
        except FileNotFoundError:
            print("\n错误：无法在当前目录下找到 'login_bilibili.py'。")
            print("请确保登录脚本与主脚本在同一个文件夹中，或手动创建 'bili_cookie.txt' 文件。")
            sys.exit(1)
        except Exception as e:
            print(f"\n错误：在尝试登录并读取 Cookie 时发生意外错误: {e}")
            sys.exit(1)

    header = {
        "Cookie": cookie,
        "User-Agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        "Referer": "https://www.bilibili.com"
    }
    return header


def get_information(bv, header):
    """通过API获取视频的 'oid' (即 'aid') 和视频标题。"""
    print(f"正在获取视频 {bv} 的信息...")
    api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bv}"
    try:
        resp = requests.get(api_url, headers=header, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get('code') == 0:
            video_data = data.get('data', {})
            oid = video_data.get('aid')
            title = video_data.get('title')
            if oid and title:
                print(f"  - [API] 成功获取: 【{title.strip()}】")
                return str(oid), title.strip()
    except Exception as e:
        print(f"  - [警告] API请求失败: {e}。")
    print(f"  - [错误] 无法通过 API 获取视频 {bv} 的信息，请检查 BV 号是否正确或 Cookie 是否有效。")
    return None, None


def md5(code):
    """对输入字符串执行 MD5 哈希。"""
    MD5 = hashlib.md5()
    MD5.update(code.encode('utf-8'))
    return MD5.hexdigest()


def fetch_latest_comments(oid, header):
    """抓取给定视频 oid 的第一页最新评论 (顶层评论)。"""
    if not oid: return []
    mixin_key_salt = "ea1db124af3c7062474693fa704f4ff8"
    params = {'oid': oid, 'type': 1, 'mode': 2, 'plat': 1, 'web_location': 1315875, 'wts': int(time.time())}
    query_for_w_rid = urllib.parse.urlencode(sorted(params.items()))
    w_rid = md5(query_for_w_rid + mixin_key_salt)
    params['w_rid'] = w_rid
    url = f"https://api.bilibili.com/x/v2/reply/wbi/main?{urllib.parse.urlencode(params)}"
    try:
        response = requests.get(url, headers=header, timeout=5)
        response.raise_for_status()
        comment_data = response.json()
        return comment_data.get('data', {}).get('replies', []) or []
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"抓取 oid={oid} 的顶层评论时出错：{e}")
    return []


def fetch_all_sub_replies(oid, root_rpid, header):
    """获取指定根评论 (root_rpid) 下的所有分页回复（子评论）。"""
    all_replies = []
    page_number = 1
    while True:
        url = f"https://api.bilibili.com/x/v2/reply/reply?oid={oid}&type=1&root={root_rpid}&pn={page_number}&ps=20"
        try:
            response = requests.get(url, headers=header, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data.get('code') == 0 and data.get('data'):
                replies = data['data'].get('replies', [])
                if not replies: break
                all_replies.extend(replies)
                page_number += 1
                time.sleep(1)
            else:
                print(f"  - [警告] 获取子评论时响应异常: {data.get('message', '未知错误')}")
                break
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            print(f"  - [错误] 请求子评论 API (root={root_rpid}) 时失败: {e}")
            break
    return all_replies


# --- 启动菜单与主逻辑 ---

def display_main_menu():
    """显示主菜单并处理用户交互，返回用户选择要监控的视频列表。"""
    header = get_header()
    selected_videos = {}

    while True:
        print("\n" + "=" * 20 + " B站评论监控菜单 " + "=" * 20)
        saved_videos = db.get_monitored_videos()
        if not saved_videos:
            print("数据库中没有已保存的视频。请先添加。")
        else:
            print("已保存的视频列表:")
            for i, (oid, bv_id, title) in enumerate(saved_videos):
                print(f"  [{i + 1}] {title} ({bv_id})")

        print("\n操作选项:")
        print("  - 输入数字 (如 1,3) 选择列表中的视频加入本次监控。")
        print("  - 输入 'a' 添加新的视频 BV 号到数据库。")
        print("  - 输入 'r' 移除数据库中的视频。")
        print("  - 输入 's' 开始监控已选择的视频。")
        print("  - 输入 'q' 退出程序。")

        if selected_videos:
            print("\n当前已选择:")
            for data in selected_videos.values():
                print(f"  -> 【{data['title']}】")

        choice = input("\n请输入您的选择: ").strip().lower()

        if choice.replace(',', '').replace(' ', '').isdigit():
            try:
                indices = [int(i.strip()) - 1 for i in choice.split(',')]
                for i in indices:
                    if 0 <= i < len(saved_videos):
                        oid, bv_id, title = saved_videos[i]
                        selected_videos[oid] = {"title": title, "bv_id": bv_id}
                        print(f"已选择: 【{title}】")
                    else:
                        print(f"错误：数字 {i + 1} 无效。")
            except ValueError:
                print("错误：请输入正确的数字格式。")

        elif choice == 'a':
            bv_input = input("请输入要添加的新 BV 号 (多个请用逗号或空格隔开): ").strip()
            bvs = [bv.strip() for bv in re.split(r'[\s,]+', bv_input) if bv.strip()]
            for bv in bvs:
                oid, title = get_information(bv, header)
                if oid and title:
                    if db.add_video_to_db(oid, bv, title):
                        print(f"成功将【{title}】添加到数据库。")
                time.sleep(1)

        elif choice == 'r':
            if not saved_videos: continue
            remove_choice = input("请输入要移除的视频编号: ").strip()
            try:
                idx = int(remove_choice) - 1
                if 0 <= idx < len(saved_videos):
                    oid_to_remove, _, title_to_remove = saved_videos[idx]
                    confirm = input(f"确定要从数据库移除【{title_to_remove}】吗? (y/n): ").lower()
                    if confirm == 'y':
                        if db.remove_video_from_db(oid_to_remove):
                            print(f"已成功移除【{title_to_remove}】。")
                            if oid_to_remove in selected_videos:
                                del selected_videos[oid_to_remove]
                        else:
                            print("移除失败。")
                else:
                    print("错误：无效的编号。")
            except ValueError:
                print("错误：请输入一个数字。")

        elif choice == 's':
            if not selected_videos:
                print("错误：您还没有选择任何要监控的视频。")
            else:
                return list(selected_videos.items())

        elif choice == 'q':
            print("程序退出。")
            sys.exit(0)

        else:
            print("无效的输入，请重新选择。")


def process_and_notify_comment(reply, oid, seen_ids, parent_user_name=None):
    """处理单条评论，检查是否为新评论，如果是，则存入数据库并返回格式化信息。"""
    rpid = reply['rpid_str']
    if rpid not in seen_ids:
        seen_ids.add(rpid)
        db.add_comment_to_db(rpid, oid)

        # 判断回复类型
        if parent_user_name:
            # B站API中，对子评论的回复会包含 at_details
            if reply.get('at_details'):
                # 遍历at列表，找到被@的人的用户名
                at_user_name = next(
                    (item['uname'] for item in reply['at_details'] if item['mid'] == reply['parent_str']),
                    parent_user_name)
                comment_type = f"回复@{at_user_name}"
            else:
                comment_type = f"回复@{parent_user_name}"
        else:
            # 主评论
            comment_type = "主评论"

        return {
            "user": reply['member']['uname'],
            "message": reply['content']['message'],
            "time": pd.to_datetime(reply["ctime"], unit='s', utc=True).tz_convert('Asia/Shanghai'),
            "type": comment_type
        }
    return None


def wait_with_manual_trigger(interval_seconds):
    """
    等待指定的秒数，同时监听用户的 Enter 键以立即触发。
    此版本兼容 Windows 和类 Unix 系统。
    """
    minutes = interval_seconds // 60
    seconds = interval_seconds % 60
    wait_message = f"等待 {minutes} 分钟 {seconds} 秒后" if minutes > 0 else f"等待 {seconds} 秒后"

    print(f"\n所有视频检查完毕。{wait_message}进行下一轮检查...")
    print("您可以随时按下 [Enter] 键来立即开始下一轮检查。")

    start_time = time.time()
    while time.time() - start_time < interval_seconds:
        # 根据操作系统使用不同的方法检测输入
        if platform.system() == "Windows":
            # msvcrt.kbhit() 是非阻塞的，它会立即返回是否有按键事件
            if msvcrt.kbhit():
                # msvcrt.getch() 会读取按键，我们检查它是否是 Enter (回车符)
                if msvcrt.getch() in [b'\r', b'\n']:
                    print("\n收到手动触发指令，立即开始新一轮检查！")
                    return  # 立即退出等待
        else:  # Linux, macOS, etc.
            # 使用 select，它在这里工作得很好
            readable, _, _ = select.select([sys.stdin], [], [], 0.1)  # 短暂等待0.1秒
            if readable:
                sys.stdin.readline()  # 清空输入缓冲区
                print("\n收到手动触发指令，立即开始新一轮检查！")
                return  # 立即退出等待

        time.sleep(0.1)  # 短暂休眠，避免 CPU 占用过高


# vvv 修改 vvv
def start_monitoring(targets_to_monitor, header, interval, webhook_enabled):
    """监控选定视频的新评论，包含获取所有子评论的功能。"""
    video_targets = {}

    print("\n" + "=" * 20 + " 初始化监控数据 " + "=" * 20)
    for oid, data in targets_to_monitor:
        print(f"正在为【{data['title']}】加载历史评论记录...")
        video_targets[oid] = {
            "title": data['title'],
            "seen_ids": db.load_seen_comments_for_video(oid)
        }
        print(f"-> 加载完成，已记录 {len(video_targets[oid]['seen_ids'])} 则历史评论。")

    print(f"\n✅ 准备就绪！开始监控 {len(video_targets)} 个视频。")
    print("=" * 55)

    while True:
        try:
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"\n[{now}] 开始新一轮检查...")

            for oid, data in video_targets.items():
                title = data['title']
                seen_ids = data['seen_ids']
                print(f"  -> 正在检查【{title}】...")

                latest_comments = fetch_latest_comments(oid, header)
                new_comments_found = []

                for comment in latest_comments:
                    new_main_comment = process_and_notify_comment(comment, oid, seen_ids)
                    if new_main_comment:
                        new_comments_found.append(new_main_comment)

                    if comment.get('replies'):
                        for sub_reply in comment['replies']:
                            new_sub_comment = process_and_notify_comment(sub_reply, oid, seen_ids,
                                                                         parent_user_name=comment['member']['uname'])
                            if new_sub_comment:
                                new_comments_found.append(new_sub_comment)

                    rcount = comment.get('rcount', 0)
                    initial_reply_count = len(comment.get('replies') or [])

                    if rcount > initial_reply_count:
                        print(f"  └── 发现【{comment['member']['uname']}】的评论有 {rcount} 条回复，正在抓取所有回复...")
                        all_sub_replies = fetch_all_sub_replies(oid, comment['rpid_str'], header)

                        for sub_reply in all_sub_replies:
                            new_hidden_comment = process_and_notify_comment(sub_reply, oid, seen_ids,
                                                                            parent_user_name=comment['member']['uname'])
                            if new_hidden_comment:
                                new_comments_found.append(new_hidden_comment)

                if new_comments_found:
                    # 对新评论按时间排序
                    sorted_comments = sorted(new_comments_found, key=lambda x: x['time'])

                    # 控制台打印
                    print("*" * 25)
                    print(f"🔥【{title}】发现 {len(sorted_comments)} 则新评论！")
                    print("*" * 25)
                    for new_comment in sorted_comments:
                        print(f"  类型: {new_comment['type']}")
                        print(f"  用户: {new_comment['user']}")
                        print(f"  评论: {new_comment['message']}")
                        print(f"  时间: {new_comment['time'].strftime('%Y-%m-%d %H:%M:%S')}")
                        print("-" * 25)

                    # 如果启用了 Webhook，则发送通知
                    if webhook_enabled:
                        notifier.send_webhook_notification(title, sorted_comments)

                time.sleep(3)  # 检查完一个视频后短暂休息，防止请求过快

            wait_with_manual_trigger(interval)

        except KeyboardInterrupt:
            print("\n程序被用户手动中断 (Ctrl+C)。再见！")
            break
        except Exception as e:
            # 增加错误类型的打印，方便调试
            print(f"\n[严重错误] 监控循环中发生未知错误 ({type(e).__name__}): {e}")
            print("等待 60 秒后重试...")
            time.sleep(60)


# ^^^ 修改 ^^^


if __name__ == "__main__":
    try:
        import requests
        import pandas
    except ImportError as e:
        print(f"缺少必要的库: {e.name}。请使用 'pip install {e.name}' 来安装它。")
        sys.exit(1)

    db.init_db()
    targets = display_main_menu()

    if targets:
        # 获取监控间隔
        interval_minutes = 5
        try:
            user_input = input(f"\n请输入检查间隔（分钟，直接按 Enter 使用默认值 {interval_minutes} 分钟）: ").strip()
            if user_input:
                interval_minutes = float(user_input)
        except ValueError:
            print(f"输入无效，将使用默认值 {interval_minutes} 分钟。")

        interval_seconds = int(interval_minutes * 60)
        if interval_seconds < 30:
            print("警告：时间间隔过短，已自动设为最低 30 秒，以避免请求过于频繁。")
            interval_seconds = 30

        # vvv 新增：Webhook 开关逻辑 vvv
        webhook_enabled = False
        # 检查配置文件是否存在且有效
        if notifier.check_webhook_configured():
            while True:
                enable_choice = input("\n检测到 Webhook 配置文件，是否启用通知功能? (y/n): ").strip().lower()
                if enable_choice == 'y':
                    webhook_enabled = True
                    print("✅ Webhook 通知已启用。")
                    break
                elif enable_choice == 'n':
                    webhook_enabled = False
                    print("❌ Webhook 通知已禁用。")
                    break
                else:
                    print("输入无效，请输入 'y' 或 'n'。")
        else:
            print("\n提示：未找到有效的 'webhook_config.txt' 文件，Webhook 通知功能将保持禁用。")
            print("如需启用，请创建该文件并在其中填入您的 Webhook URL。")
        # ^^^ 新增 ^^^

        header = get_header()
        # 修改：传入 webhook_enabled 参数
        start_monitoring(targets, header, interval_seconds, webhook_enabled)
