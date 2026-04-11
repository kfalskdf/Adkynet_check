#!/bin/bash
# =============================================================================
# CF Tunnel 监控脚本 - 青龙面板版
# 功能：
#   1. 检查 CF Tunnel 状态（通过 Cloudflare API）
#   2. Tunnel 正常 → 发送 OK 到 Gotify，退出
#   3. Tunnel 异常 → 登录 manager.adkynet.com 检查到期日
#   4. 已到期(3天内) → 发送到期提醒
#   5. 未到期 → 登录 panel.adkynet.com 检查 CPU，发送提醒
# =============================================================================

set -e

# ============================================
# 配置检查
# ============================================
check_env() {
    local missing=""
    for var in CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_TUNNEL_ID \
               MANAGER_USER MANAGER_PASS PANEL_USER PANEL_PASS \
               GOTIFY_URL GOTIFY_TOKEN; do
        if [ -z "${!var}" ]; then
            missing="$missing $var"
        fi
    done
    
    if [ -n "$missing" ]; then
        echo "[ERROR] Missing required env vars:$missing"
        exit 1
    fi
}

# ============================================
# 发送通知到 Gotify
# ============================================
send_notification() {
    local title="$1"
    local message="$2"
    local priority="${3:-5}"
    
    curl -s -X POST "${GOTIFY_URL}/message?token=${GOTIFY_TOKEN}" \
        -F "title=${title}" \
        -F "message=${message}" \
        -F "priority=${priority}" || true
    
    echo "[NOTIFY] $title - $message"
}

# ============================================
# 步骤1: 检查 Tunnel 状态 (CF API)
# ============================================
check_tunnel_status() {
    echo "[INFO] Checking CF Tunnel status..."
    
    local response
    response=$(curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
        "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel/$CLOUDFLARE_TUNNEL_ID")
    
    local status
    status=$(echo "$response" | jq -r '.result.status // "unknown"')
    
    echo "[INFO] Tunnel status: $status"
    echo "$response" | jq -r '.result.connections'
    
    if [ "$status" == "healthy" ]; then
        send_notification "CF Tunnel OK" "Tunnel 状态正常" 1
        exit 0
    fi
    
    return 1  # Tunnel 异常
}

# ============================================
# 步骤2: 登录 manager.adkynet.com 检查到期日
# ============================================
check_expiry_date() {
    echo "[INFO] Checking expiry date on manager.adkynet.com..."
    
    local cookie_jar
    cookie_jar=$(mktemp)
    
    # 获取登录页面，提取 token
    local login_page
    login_page=$(curl -s -c "$cookie_jar" "https://manager.adkynet.com/login")
    
    local token
    token=$(echo "$login_page" | grep -oP 'name="token"[^>]*value="\K[^"]+' | head -1)
    
    if [ -z "$token" ]; then
        token=$(echo "$login_page" | grep -oP 'token"[^>]*value="\K[^"]+' | head -1)
    fi
    
    echo "[DEBUG] Login token: ${token:0:20}..."
    
    # 执行登录
    curl -s -L -c "$cookie_jar" -b "$cookie_jar" \
        -d "username=${MANAGER_USER}" \
        -d "password=${MANAGER_PASS}" \
        -d "token=${token}" \
        "https://manager.adkynet.com/login" > /dev/null
    
    # 获取产品详情页
    local detail_page
    detail_page=$(curl -s -L -b "$cookie_jar" \
        "https://manager.adkynet.com/clientarea.php?action=productdetails&id=38143")
    
    echo "[DEBUG] Detail page length: ${#detail_page}"
    
    # 提取下次到期日 (需要根据实际页面调整)
    # 常见模式: 2024-12-31 或 2024/12/31
    local next_due_date
    next_due_date=$(echo "$detail_page" | grep -oP '下次到期日[:\s]*\K[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}' | head -1)
    
    if [ -z "$next_due_date" ]; then
        next_due_date=$(echo "$detail_page" | grep -oP '[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}' | tail -1)
    fi
    
    echo "[INFO] Next due date: $next_due_date"
    
    # 计算到期天数
    if [ -n "$next_due_date" ]; then
        local due_timestamp now_timestamp days_until
        due_timestamp=$(date -d "$next_due_date" +%s 2>/dev/null || echo "0")
        now_timestamp=$(date +%s)
        days_until=$(( (due_timestamp - now_timestamp) / 86400 ))
        
        echo "[INFO] Days until expiry: $days_until"
        
        # 3天内到期或已到期
        if [ "$days_until" -le 3 ]; then
            send_notification "CF Tunnel 到期提醒" "服务将在 $next_due_date 到期 (剩余 $days_until 天)"
            rm -f "$cookie_jar"
            exit 0
        fi
    fi
    
    rm -f "$cookie_jar"
    return 1  # 未到期
}

# ============================================
# 步骤3: 登录 panel.adkynet.com 检查 CPU
# ============================================
check_cpu_load() {
    echo "[INFO] Checking CPU load on panel.adkynet.com..."
    
    local cookie_jar
    cookie_jar=$(mktemp)
    
    # 获取登录页面
    local login_page
    login_page=$(curl -s -c "$cookie_jar" "https://panel.adkynet.com/")
    
    local token
    token=$(echo "$login_page" | grep -oP 'name="token"[^>]*value="\K[^"]+' | head -1)
    
    # 登录
    curl -s -L -c "$cookie_jar" -b "$cookie_jar" \
        -d "username=${PANEL_USER}" \
        -d "password=${PANEL_PASS}" \
        -d "token=${token}" \
        "https://panel.adkynet.com/" > /dev/null
    
    # 获取服务器详情
    local server_page
    server_page=$(curl -s -L -b "$cookie_jar" \
        "https://panel.adkynet.com/server/37268689")
    
    echo "[DEBUG] Server page length: ${#server_page}"
    
    # 提取 CPU load (需要根据实际页面调整)
    # 常见模式: "CPU Load: 45%" 或 "cpu" 后面的数字
    local cpu_load
    cpu_load=$(echo "$server_page" | grep -oP 'CPU[:\s]+(?:Load[:\s]+)?\K[0-9]+' | head -1)
    
    if [ -z "$cpu_load" ]; then
        cpu_load=$(echo "$server_page" | grep -oP 'cpu[^\d]*\K[0-9]+' | head -1)
    fi
    
    if [ -z "$cpu_load" ]; then
        cpu_load=$(echo "$server_page" | grep -oP '[0-9]+(?=%.*CPU)' | head -1)
    fi
    
    echo "[INFO] CPU Load: ${cpu_load}%"
    
    # 只要有 CPU 数据就发送提醒
    if [ -n "$cpu_load" ] && [ "$cpu_load" -gt 0 ]; then
        send_notification "CF Tunnel CPU 告警" "Tunnel 断开，CPU 使用率: ${cpu_load}%"
    else
        send_notification "CF Tunnel 异常" "Tunnel 断开，无法获取 CPU 信息"
    fi
    
    rm -f "$cookie_jar"
}

# ============================================
# 主流程
# ============================================
main() {
    echo "========================================"
    echo "CF Tunnel Monitor - $(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"
    
    check_env
    
    # 步骤1: 检查 Tunnel 状态
    if check_tunnel_status; then
        echo "[DONE] Tunnel 正常，任务完成"
        exit 0
    fi
    
    # 步骤2: Tunnel 异常，检查到期日
    if check_expiry_date; then
        echo "[DONE] 已发送到期提醒"
        exit 0
    fi
    
    # 步骤3: 未到期，检查 CPU
    check_cpu_load
    
    echo "[DONE] 任务完成"
}

main "$@"