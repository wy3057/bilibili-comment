import requests
import qrcode
import time
import sys

# --- Bilibili API URLs ---
# 1. 获取二维码URL和密钥的API
QR_GENERATE_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
# 2. 使用密钥轮询扫码状态的API
QR_POLL_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"


# 只需要修改这个函数
def generate_and_show_qrcode():
    """
    请求B站API生成二维码，并将其保存为图片文件。
    返回二维码的密钥 qrcode_key。
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(QR_GENERATE_API, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data['code'] == 0:
            login_url = data['data']['url']
            qrcode_key = data['data']['qrcode_key']

            print("二维码获取成功，请打开脚本同目录下的 'qrcode.png' 文件进行扫描。")

            # --- 这是修改的核心部分 ---
            # 直接使用 qrcode.make() 更简洁地创建二维码图片
            img = qrcode.make(login_url)
            # 将图片保存到文件
            img.save("qrcode.png")
            img.show()
            # --- 修改结束 ---

            return qrcode_key
        else:
            print(f"错误：获取二维码失败。B站返回信息: {data.get('message', '未知错误')}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"网络错误：无法连接到B站服务器。 {e}")
        return None


def poll_for_login_status(qrcode_key):
    """
    使用qrcode_key轮询登录状态，直到成功、失败或超时。
    成功后返回包含cookie的requests.Session对象。
    """
    # 定义请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # 使用Session对象来自动管理和保存登录成功后的cookie
    session = requests.Session()
    # 将请求头设置给整个 session，后续所有请求都会带上
    session.headers.update(headers)

    scan_confirmed_message_shown = False

    print("\n正在等待扫码和确认...")

    try:
        while True:
            poll_url = f"{QR_POLL_API}?qrcode_key={qrcode_key}"
            # 使用 session 发起请求，会自动带上 headers
            response = session.get(poll_url)
            response.raise_for_status()
            data = response.json()

            # ... (后续代码逻辑不变)
            status_code = data['data']['code']

            if status_code == 0:
                print("\n登录成功！")
                return session
            elif status_code == 86090:
                if not scan_confirmed_message_shown:
                    print("\n扫描成功！请在手机上点击确认登录。")
                    scan_confirmed_message_shown = True
            elif status_code == 86101:
                pass
            elif status_code == 86038:
                print("\n错误：二维码已失效或超时，请重新运行程序。")
                return None
            else:
                print(f"\n未知状态码: {status_code}, message: {data['data']['message']}")
                return None

            time.sleep(2)
            print(".", end="", flush=True)

    except requests.exceptions.RequestException as e:
        print(f"\n网络错误：轮询登录状态时出错。 {e}")
        return None
    except KeyboardInterrupt:
        print("\n用户中断了操作。")
        return None


def save_cookie_from_session(session, filename="bili_cookie.txt"):
    """
    从成功的session中提取cookies并保存到文件。
    """
    if not session:
        return False

    # 将session中的cookie字典转换为标准的cookie字符串
    cookie_dict = session.cookies.get_dict()
    cookie_str = '; '.join([f"{k}={v}" for k, v in cookie_dict.items()])

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(cookie_str)
        print(f"Cookie已成功保存到文件: {filename}")
        return True
    except IOError as e:
        print(f"错误：无法写入Cookie文件。 {e}")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print(" Bilibili 扫码登录程序")
    print("=" * 50)

    # 步骤1: 获取并显示二维码
    key = generate_and_show_qrcode()

    if key:
        # 步骤2: 轮询登录状态
        login_session = poll_for_login_status(key)

        # 步骤3: 如果登录成功，保存cookie
        if login_session:
            save_cookie_from_session(login_session)
        else:
            print("\n登录过程失败或被取消。")

    sys.exit("\n程序执行完毕。")

