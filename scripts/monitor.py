#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CF Tunnel 监控脚本 - 青龙面板版 (Python)
功能：
  1. 检查 CF Tunnel 状态（通过 Cloudflare API）
  2. Tunnel 正常 → 发送 OK 到 Gotify，退出
  3. Tunnel 异常 → 用 Cookie + Selenium 登录 manager.adkynet.com 检查到期日
  4. 已到期(3天内) → 发送到期提醒
  5. 未到期 → 用 Selenium 登录 panel.adkynet.com 检查 CPU，发送提醒
"""

import os
import sys
import re
import json
import time
from datetime import datetime
from typing import Optional, Tuple

import requests

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


class CFMonitor:
    """CF Tunnel 监控类"""
    
    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    
    def __init__(self):
        # 从环境变量获取配置
        self.cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
        self.cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.cf_tunnel_id = os.getenv("CLOUDFLARE_TUNNEL_ID", "")
        
        # Adkynet 用户名 (manager 和 panel 使用相同用户名)
        self.adkynet_user = os.getenv("ADKYNET_USER", "")
        # Adkynet 密码 (manager 和 panel 使用不同密码)
        self.manager_pass = os.getenv("MANAGER_PASS", "")
        self.panel_pass = os.getenv("PANEL_PASS", "")
        
        self.gotify_url = os.getenv("GOTIFY_URL", "")
        self.gotify_token = os.getenv("GOTIFY_TOKEN", "")
    
    def check_env(self) -> bool:
        """检查必需的环境变量"""
        required = [
            "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_TUNNEL_ID",
            "ADKYNET_USER", "MANAGER_PASS", "PANEL_PASS",
            "GOTIFY_URL", "GOTIFY_TOKEN"
        ]
        missing = [var for var in required if not os.getenv(var)]
        
        if missing:
            print(f"[错误] 缺少必需的环境变量: {', '.join(missing)}")
            return False
        return True
    
    def send_notification(self, title: str, message: str, priority: int = 5) -> bool:
        """发送通知到 Gotify"""
        if not self.gotify_url or not self.gotify_token:
            print("[错误] 未配置 Gotify")
            return False
        
        url = f"{self.gotify_url}/message?token={self.gotify_token}"
        data = {
            "title": title,
            "message": message,
            "priority": priority
        }
        
        try:
            resp = requests.post(url, data=data, timeout=10)
            print(f"[通知] {title} - {message} (状态码: {resp.status_code})")
            return True
        except Exception as e:
            print(f"[错误] 发送通知失败: {e}")
            return False
    
    def check_tunnel_status(self) -> Tuple[bool, Optional[str]]:
        """检查 Tunnel 状态 (CF API)"""
        print("[信息] 正在检查 Cloudflare Tunnel 状态...")
        
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/cfd_tunnel/{self.cf_tunnel_id}"
        headers = {"Authorization": f"Bearer {self.cf_token}"}
        
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            
            status = data.get("result", {}).get("status", "unknown")
            print(f"[信息] Tunnel 状态: {status}")
            
            connections = data.get("result", {}).get("connections", [])
            if connections:
                print(f"[信息] 活跃连接数: {len(connections)}")
            
            if status == "healthy":
                self.send_notification("CF Tunnel 正常", "Tunnel 状态正常", 1)
                return True, status
            
            return False, status
            
        except Exception as e:
            print(f"[错误] 检查 Tunnel 状态失败: {e}")
            return False, None
    
    def _create_driver(self):
        """创建 Chrome 驱动"""
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-images")
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(60)
        return driver
    
    def _parse_cookie(self, cookie_str: str) -> dict:
        """解析 Cookie 字符串为字典"""
        cookies = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                cookies[key.strip()] = value.strip()
        return cookies
    
    def _retry_until_success(self, func, *args, **kwargs):
        """重试直到成功"""
        last_error = None
        
        for attempt in range(1, self.MAX_RETRIES + 1):
            print(f"[信息] 第 {attempt}/{self.MAX_RETRIES} 次尝试...")
            try:
                result = func(*args, **kwargs)
                if result:
                    return result
                print(f"[警告] 第 {attempt} 次尝试未成功，等待 {self.RETRY_DELAY} 秒后重试...")
            except Exception as e:
                last_error = e
                print(f"[错误] 第 {attempt} 次尝试失败: {e}，等待 {self.RETRY_DELAY} 秒后重试...")
            
            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY)
        
        return None
    
    def check_expiry_date(self) -> Tuple[bool, Optional[int]]:
        """使用 Selenium 登录 manager.adkynet.com 检查到期日（带重试）"""
        print("[信息] 正在检查服务到期日...")
        
        if not self.adkynet_user or not self.manager_pass:
            print("[错误] 未配置 ADKYNET_USER 或 MANAGER_PASS")
            return False, None
        
        if not SELENIUM_AVAILABLE:
            print("[错误] Selenium 未安装")
            return False, None
        
        def _do_check():
            driver = None
            try:
                driver = self._create_driver()
                
                # 1. 访问登录页
                print("[信息] 正在访问 manager.adkynet.com...")
                driver.get("https://manager.adkynet.com/login?language=english")
                time.sleep(5)
                
                # 2. 输入用户名 (username 字段)
                print("---[信息] 正在输入用户名...")
                try:
                    username_input = WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.NAME, "username"))
                    )
                    username_input.clear()
                    username_input.send_keys(self.adkynet_user)
                except TimeoutException:
                    print("---[错误] 找不到用户名输入框")
                    raise Exception("找不到用户名输入框")
                
                # 3. 输入密码
                password_input = driver.find_element(By.NAME, "password")
                password_input.clear()
                password_input.send_keys(self.manager_pass)
                
                # 4. 点击登录按钮
                print("---[信息] 正在点击登录...")
                try:
                    submit_button = driver.find_element(By.XPATH, "/html/body/section[3]/div/div/div[1]/div/div[3]/div[1]/form/div[5]/input")
                    submit_button.click()
                except NoSuchElementException:
                    print("---[错误] 找不到登录按钮")
                    raise Exception("找不到登录按钮")
                
                # 等待页面加载
                time.sleep(8)
                
                current_url = driver.current_url
                print(f"---[信息] 登录后 URL: {current_url}")
                
                # 检查是否登录成功
                if "login" in current_url.lower():
                    # 抓取错误提示
                    print("---[错误] 登录失败，正在抓取错误提示...")
                    try:
                        error_element = driver.find_element(By.XPATH, "/html/body/section[3]/div/div/div[1]/div/div[2]")
                        error_text = error_element.text
                        print(f"---[调试] 错误提示: {error_text}")
                        raise Exception(f"登录失败: {error_text}")
                    except NoSuchElementException:
                        raise Exception("登录失败")
                
                print("[信息] manager.adkynet.com 登录成功")
                
                # 5. 访问产品详情页
                print("---[信息] 正在访问产品详情页...")
                driver.get("https://manager.adkynet.com/clientarea.php?action=productdetails&id=38143")
                time.sleep(5)
                
                # 6. 提取下次到期日 (XPath)
                print("---[信息] 正在提取到期日...")
                try:
                    expiry_element = driver.find_element(By.XPATH, "/html/body/section[3]/div/div/div[3]/div/div[1]/div[1]/div/div[2]")
                    expiry_text = expiry_element.text
                    print(f"---[调试] 到期日元素文本: {expiry_text}")
                    
                    # 提取英文日期格式 (如 1st May 2026)
                    date_match = re.search(r'Next Due Date\s*\n?\s*(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})', expiry_text, re.IGNORECASE)
                    
                    if date_match:
                        day = date_match.group(1)
                        month = date_match.group(2)
                        year = date_match.group(3)
                        
                        month_map = {
                            'january': '01', 'jan': '01', 'february': '02', 'feb': '02',
                            'march': '03', 'mar': '03', 'april': '04', 'apr': '04',
                            'may': '05', 'june': '06', 'jun': '06', 'july': '07', 'jul': '07',
                            'august': '08', 'aug': '08', 'september': '09', 'sep': '09', 'sept': '09',
                            'october': '10', 'oct': '10', 'november': '11', 'nov': '11',
                            'december': '12', 'dec': '12'
                        }
                        month_num = month_map.get(month.lower(), '01')
                        next_due_date = f"{year}/{month_num}/{day.zfill(2)}"
                    else:
                        next_due_date = ""
                        
                except Exception as e:
                    print(f"---[警告] XPath 提取失败: {e}")
                    next_due_date = ""
                
                print(f"---[信息] 下次到期日: {next_due_date}")
                
                if next_due_date:
                    display_date = next_due_date
                    parse_date = next_due_date.replace("/", "-")
                    due_date = datetime.strptime(parse_date, "%Y-%m-%d")
                    days_until = (due_date - datetime.now()).days
                    
                    print(f"---[信息] 距离到期还有: {days_until} 天")
                    
                    if days_until <= 3:
                        self.send_notification(
                            "Adkynet 服务到期提醒",
                            f"服务将在 {display_date} 到期 (剩余 {days_until} 天)"
                        )
                        return True, days_until
                
                return False, None
                
            except Exception as e:
                print(f"---[错误] 访问失败: {e}")
                raise e
                
            finally:
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
        
        result = self._retry_until_success(_do_check)
        
        if result is None:
            self.send_notification("Adkynet 监控错误", "manager.adkynet.com 多次尝试失败")
            print("---[错误] 多次尝试后仍未成功，任务退出")
            sys.exit(1)
        
        return result if result else (False, None)
    
    def check_cpu_load(self) -> Optional[str]:
        """使用 Selenium 登录 panel.adkynet.com 检查 服务器状态（带重试）"""
        print("[信息] 正在检查 服务器状态...")
        
        if not SELENIUM_AVAILABLE:
            print("---[错误] Selenium 未安装")
            self.send_notification("Adkynet 监控错误", "Selenium 未安装")
            return None
        
        def _do_check():
            driver = None
            try:
                driver = self._create_driver()
                
                # 1. 访问登录页
                print("---[信息] 正在访问 panel.adkynet.com...")
                driver.get("https://panel.adkynet.com/")
                time.sleep(5)
                
                # 2. 输入用户名
                print("---[信息] 正在输入用户名...")
                try:
                    username_input = WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.NAME, "username"))
                    )
                    username_input.clear()
                    username_input.send_keys(self.adkynet_user)
                except TimeoutException:
                    print("---[错误] 找不到用户名输入框")
                    raise Exception("找不到用户名输入框")
                
                # 3. 输入密码
                password_input = driver.find_element(By.NAME, "password")
                password_input.clear()
                password_input.send_keys(self.manager_pass)
                
                # 4. 点击登录按钮
                print("---[信息] 正在点击登录...")
                try:
                    submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                    submit_button.click()
                except NoSuchElementException:
                    print("---[错误] 找不到登录按钮")
                    raise Exception("找不到登录按钮")
                
                # 等待页面加载
                time.sleep(8)
                
                current_url = driver.current_url
                print(f"---[信息] 登录后 URL: {current_url}")
                
                # 检查是否登录成功
                if "login" in current_url.lower():
                    print("---[错误] 登录失败，请检查用户名密码")
                    raise Exception("登录失败")
                
                print("[信息] panel.adkynet.com 登录成功")
                
                # 5. 访问服务器详情页
                print("[信息] 正在访问服务器详情页...")
                driver.get("https://panel.adkynet.com/server/37268689")
                time.sleep(5)
                
                page_source = driver.page_source
                print(f"---[调试] 服务器详情页长度: {len(page_source)}")
                
                # 6. 提取服务器状态 (使用 XPath)
                print("---[信息] 正在提取服务器状态...")
                try:
                    status_element = driver.find_element(By.XPATH, "/html/body/div[2]/div[2]/div[4]/section/div[1]/div[2]/div[2]/div[2]/div[3]/div")
                    status_text = status_element.text
                    print(f"---[调试] 状态元素文本: {status_text}")
                    
                    # 提取状态值 (Online/Offline 等)
                    status = status_text.strip()
                    
                except Exception as e:
                    print(f"---[警告] XPath 提取状态失败: {e}")
                    status = ""
                
                print(f"---[信息] 服务器状态: {status}")
                
                # 发送提醒
                if status:
                    self.send_notification(
                        "Adkynet 服务器状态",
                        f"Tunnel 已断开，服务器状态: {status}"
                    )
                else:
                    self.send_notification(
                        "Adkynet 异常",
                        "Tunnel 已断开，无法获取服务器状态"
                    )
                
                return status
                
            except Exception as e:
                print(f"---[错误] Selenium 执行失败: {e}")
                raise e
                
            finally:
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
        
        result = self._retry_until_success(_do_check)
        
        if result is None:
            self.send_notification("Adkynet 监控错误", "panel.adkynet.com 多次尝试失败")
            print("---[错误] 多次尝试后仍未成功，任务退出")
            sys.exit(1)
        
        return result
    
    def run(self):
        """主流程"""
        print("=" * 50)
        print(f"Adkynet 监控 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50)
        
        if not self.check_env():
            sys.exit(1)
        
        # 步骤1: 检查 Tunnel 状态
        is_healthy, status = self.check_tunnel_status()
        if is_healthy:
            print("[完成] Tunnel 正常，任务结束")
            sys.exit(0)
        
        # 步骤2: Tunnel 异常，检查到期日（带重试）
        is_expired, days = self.check_expiry_date()
        if is_expired:
            print("[完成] 已发送到期提醒")
            sys.exit(0)
        
        # 步骤3: 未到期，检查 服务状态（带重试）
        self.check_cpu_load()
        
        print("[完成] 任务结束")


if __name__ == "__main__":
    CFMonitor().run()