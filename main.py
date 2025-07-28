import re
import sys
import requests
import json
import hashlib
import urllib.parse
import time
import datetime
import pandas as pd
import subprocess  # 新增：用於執行外部腳本

# --- 全局變數 ---
# 用於儲存所有已見評論 ID 的集合，防止重複通知
seen_comment_ids = set()


# --- 核心功能函數 ---

def get_header():
    """
    從 'bili_cookie.txt' 讀取 cookie 並建構請求標頭。
    如果文件不存在或為空，則嘗試調用 login_bilibili.py 進行登錄，然後重試。
    """
    try:
        with open('bili_cookie.txt', 'r', encoding='utf-8') as f:
            cookie = f.read().strip()
        if not cookie:
            # 如果文件是空的，也當作「未找到」處理，進入 except 區塊
            raise FileNotFoundError("Cookie 文件為空。")
    except FileNotFoundError:
        print("提示：'bili_cookie.txt' 文件未找到或為空。")
        print("正在嘗試調用 'login_bilibili.py' 進行自動登錄...")

        try:
            # 使用 subprocess 執行登錄腳本
            # sys.executable確保使用當前環境的Python解釋器
            subprocess.run(
                [sys.executable, 'login_bilibili.py'],
                check=False,  # 如果腳本返回非零退出碼（表示錯誤），則會引發 CalledProcessError
                encoding='utf-8'
            )
            print("登錄腳本執行完畢，將重新讀取 Cookie。")

            # 登錄腳本成功執行後，再次嘗試讀取 cookie
            with open('bili_cookie.txt', 'r', encoding='utf-8') as f:
                cookie = f.read().strip()
            if not cookie:
                print("錯誤：登錄後 'bili_cookie.txt' 仍然為空，請手動檢查登錄過程是否成功。")
                sys.exit(1)

        except FileNotFoundError:
            print("\n錯誤：無法在當前目錄下找到 'login_bilibili.py'。")
            print("請確保登錄腳本與主腳本在同一個文件夾中，或手動創建 'bili_cookie.txt' 文件。")
            sys.exit(1)
        except subprocess.CalledProcessError:
            print("\n錯誤：'login_bilibili.py' 執行時發生錯誤。")
            print("請檢查登錄腳本的功能是否正常，或手動創建 cookie 文件。")
            sys.exit(1)
        except Exception as e:
            print(f"\n錯誤：在嘗試登錄並讀取 Cookie 時發生意外錯誤: {e}")
            sys.exit(1)

    # 成功獲取 cookie 後，構建並返回請求標頭
    header = {
        "Cookie": cookie,
        "User-Agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        "Referer": "https://www.bilibili.com"
    }
    return header


def get_information(bv, header):
    """
    通过API或网页抓取来获取视频的 'oid' (即 'aid') 和视频标题。
    优先使用API，失败后尝试网页抓取。
    """
    print(f"正在獲取影片 {bv} 的資訊...")
    # 方案一：使用Web API (更穩定)
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
                print(f"  - [API] 成功獲取: {title}")
                return str(oid), title.strip()
    except Exception as e:
        print(f"  - [警告] API請求失敗: {e}。正在嘗試備用方案...")

    # 方案二：如果API失敗，則抓取網頁源碼 (作為備用)
    page_url = f"https://www.bilibili.com/video/{bv}/"
    try:
        resp = requests.get(page_url, headers=header, timeout=5)
        resp.raise_for_status()
        html_content = resp.text

        # 提取影片 oid (aid)
        oid_match = re.search(r'"aid"\s*:\s*(\d+)', html_content)
        # 提取影片標題
        title_match = re.search(r'<title data-vue-meta="true">(.*?)_哔哩哔哩_bilibili</title>', html_content)

        if oid_match and title_match:
            oid = oid_match.group(1)
            title = title_match.group(1)
            print(f"  - [備用方案] 成功抓取: {title}")
            return str(oid), title.strip()
        else:
            print(f"  - [錯誤] 備用方案也無法從頁面源碼中找到aid或title for BV: {bv}")
            return None, None
    except requests.exceptions.RequestException as e:
        print(f"  - [錯誤] 備用抓取方案失敗: {e}")
        return None, None


def md5(code):
    """對輸入字串執行 MD5 雜湊。"""
    MD5 = hashlib.md5()
    MD5.update(code.encode('utf-8'))
    return MD5.hexdigest()


def fetch_latest_comments(oid, header):
    """
    使用 w_rid 簽名方式，擷取給定影片 oid 的第一頁最新評論。
    這是目前 Bilibili Web 端使用的方法。
    """
    if not oid:
        return []

    # 固定的 mixinKey，用於 w_rid 的計算
    mixin_key_salt = "ea1db124af3c7062474693fa704f4ff8"

    # 準備用於 w_rid 生成的參數
    params = {
        'oid': oid,
        'type': 1,
        'mode': 2,  # 模式 2 代表按時間倒序（最新）
        'plat': 1,
        'web_location': 1315875,
        'wts': int(time.time())
    }

    # 步驟 1: 對參數的鍵值對進行排序並編碼
    query_for_w_rid = urllib.parse.urlencode(sorted(params.items()))
    # 步驟 2: 拼接固定的 mixinKey
    query_for_w_rid += mixin_key_salt
    # 步驟 3: 計算 MD5 得到 w_rid
    w_rid = md5(query_for_w_rid)

    # 將計算出的 w_rid 加入到最終的請求參數中
    params['w_rid'] = w_rid

    # 構造最終請求 URL
    url = f"https://api.bilibili.com/x/v2/reply/wbi/main?{urllib.parse.urlencode(params)}"

    try:
        response = requests.get(url, headers=header)
        response.raise_for_status()
        comment_data = response.json()
        # 安全地提取評論列表，如果不存在則返回空列表
        return comment_data.get('data', {}).get('replies', []) or []
    except requests.exceptions.RequestException as e:
        print(f"擷取評論時出錯：{e}")
    except json.JSONDecodeError:
        print("解碼評論 JSON 回應時出錯。可能是 cookie 失效或被風控。")
    return []


def monitor_comments(bv):
    """
    監控新評論的主要功能，包含初始化和無限循環。
    """
    header = get_header()
    oid, title = get_information(bv, header)
    if not oid:
        print("無法獲取影片資訊，程序終止。")
        return

    print(f"\n✅ 準備就緒！開始監控影片:【{title}】(oid: {oid})")
    print("=" * 50)

    # 首次運行時，先獲取一次評論，將其全部標記為已讀
    print("首次運行，正在初始化評論列表...")
    initial_comments = fetch_latest_comments(oid, header)
    for comment in initial_comments:
        seen_comment_ids.add(comment['rpid_str'])
        # 同時處理根評論下的子評論
        if 'replies' in comment and comment['replies']:
            for sub_comment in comment['replies']:
                seen_comment_ids.add(sub_comment['rpid_str'])
    print(f"初始化完成，已記錄 {len(seen_comment_ids)} 則現有評論。")

    while True:
        try:
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"\n[{now}] 正在檢查新評論...")

            latest_comments = fetch_latest_comments(oid, header)

            new_comments_found = []

            for comment in latest_comments:
                rpid = comment['rpid_str']
                # 檢查主評論
                if rpid not in seen_comment_ids:
                    seen_comment_ids.add(rpid)
                    new_comment_info = {
                        "user": comment['member']['uname'],
                        "message": comment['content']['message'],
                        "time": pd.to_datetime(comment["ctime"], unit='s', utc=True).tz_convert('Asia/Taipei'),
                        "type": "主評論"
                    }
                    new_comments_found.append(new_comment_info)

                # 檢查該主評論下的子評論
                if 'replies' in comment and comment['replies']:
                    for sub_comment in comment['replies']:
                        sub_rpid = sub_comment['rpid_str']
                        if sub_rpid not in seen_comment_ids:
                            seen_comment_ids.add(sub_rpid)
                            sub_comment_info = {
                                "user": sub_comment['member']['uname'],
                                "message": sub_comment['content']['message'],
                                "time": pd.to_datetime(sub_comment["ctime"], unit='s', utc=True).tz_convert(
                                    'Asia/Taipei'),
                                "type": f"回覆@{comment['member']['uname']}"
                            }
                            new_comments_found.append(sub_comment_info)

            if new_comments_found:
                print("*" * 20)
                print(f"🔥 發現 {len(new_comments_found)} 則新評論！")
                print("*" * 20)
                for new_comment in sorted(new_comments_found, key=lambda x: x['time']):  # 按時間排序顯示
                    print(f"  類型: {new_comment['type']}")
                    print(f"  用戶: {new_comment['user']}")
                    print(f"  評論: {new_comment['message']}")
                    print(f"  時間: {new_comment['time'].strftime('%Y-%m-%d %H:%M:%S')}")
                    print("-" * 20)
            else:
                print("✔️ 本次更新中沒有新評論。")

            # 等待下一次檢查
            interval = 300  # 300 秒 = 5 分鐘
            print(f"等待 {interval // 60} 分鐘後進行下一次檢查...")
            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n程序被用戶手動中斷。再見！")
            break
        except Exception as e:
            print(f"\n[嚴重錯誤] 監控循環中發生未知錯誤: {e}")
            print("等待 60 秒後重試...")
            time.sleep(60)


if __name__ == "__main__":
    # 確保您已經安裝了必要的庫
    try:
        import requests
        import pandas
    except ImportError as e:
        print(f"缺少必要的庫: {e.name}。")
        print(f"請使用 'pip install {e.name}' 來安裝它。")
        sys.exit(1)

    bv_id = input("請輸入要監控的影片 BV 號 (例如 BV1xP411A7A4): ").strip()
    if bv_id:
        monitor_comments(bv_id)
    else:
        print("未輸入有效的 BV 號。")
