// --- 全局状态变量 ---
let initialUserHash, allStocks;
const klineDataCache = {};
let myChart = null;
let currentPeriod = '1d';
let isLoggedIn = false;
let authToken = null;
let currentUserId = null;
let currentUserHashForKline = '';

// --- ECharts 核心渲染函数 (V2: 带MA线) ---
function renderChart(stockName, stockId, klineData) {
    const periodMap = { '1d': '最近 288 K线', '7d': '最近 2016 K线', '30d': '最近 30 天 (小时K)' };
    const dataPeriod = periodMap[currentPeriod] || '自定义周期';

    // 定义将在图表中使用的最终数据变量
    let dates, klineValues, ma5Data, ma10Data, ma30Data;
    
    const all_kline_history = klineData.kline_history || [];

    // 计算MA均线的辅助函数
    function calculateMA(dayCount, data) {
        let result = [];
        for (let i = 0, len = data.length; i < len; i++) {
            if (i < dayCount - 1) {
                result.push('-');
                continue;
            }
            let sum = 0;
            for (let j = 0; j < dayCount; j++) {
                sum += parseFloat(data[i - j][1]);
            }
            result.push((sum / dayCount).toFixed(2));
        }
        return result;
    }

    // 根据当前周期选择不同的数据处理方式
    if (currentPeriod === '1d') {
        // --- 仅对 1D 视图执行 padding 数据处理逻辑 ---
        const padding = 29;
        
        const all_dates = all_kline_history.map(item => item.date);
        const all_klineValues = all_kline_history.map(item => [item.open, item.close, item.low, item.high]);
        
        const ma5Data_full = calculateMA(5, all_klineValues);
        const ma10Data_full = calculateMA(10, all_klineValues);
        const ma30Data_full = calculateMA(30, all_klineValues);
        
        const hasPaddingData = all_dates.length > padding;

        dates = hasPaddingData ? all_dates.slice(padding) : all_dates;
        klineValues = hasPaddingData ? all_klineValues.slice(padding) : all_klineValues;
        ma5Data = hasPaddingData ? ma5Data_full.slice(padding) : ma5Data_full;
        ma10Data = hasPaddingData ? ma10Data_full.slice(padding) : ma10Data_full;
        ma30Data = hasPaddingData ? ma30Data_full.slice(padding) : ma30Data_full;

    } else {
        // --- 对 7D 和 30D 视图使用原始的、不带 padding 的处理逻辑 ---
        dates = all_kline_history.map(item => item.date);
        klineValues = all_kline_history.map(item => [item.open, item.close, item.low, item.high]);
        ma5Data = calculateMA(5, klineValues);
        ma10Data = calculateMA(10, klineValues);
        ma30Data = calculateMA(30, klineValues);
    }
    // ▲▲▲ 修改结束 ▲▲▲

    const isMobile = window.innerWidth < 768;
    let gridOption = isMobile ? { left: 50, right: 15, bottom: 80, top: 55 } : { left: '8%', right: '8%', bottom: '20%', top: '15%' };
    let dataZoomOption = [{ type: 'inside' }, { show: true, type: 'slider', bottom: '10%', height: 25, textStyle: { color: '#e0e0e0' } }];
    
    let avgCostLine = [];
    if (klineData.user_holdings && klineData.user_holdings.length > 0) {
        const holding = klineData.user_holdings[0];
        avgCostLine.push({ name: '平均成本', yAxis: holding.avg_cost, lineStyle: { color: '#00ccff', type: 'dashed' }, label: { formatter: '{b}: {c}', position: 'insideEndTop', color: '#00ccff' } });
    }
    
    // ECharts option 配置 (这部分无需修改)
    const option = {
        backgroundColor: 'transparent',
        title: { text: `${stockName} (${stockId})`, subtext: dataPeriod, left: 'center', textStyle: { color: '#e0e0e0' }, subtextStyle: { color: '#888' } },
        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: function (params) { var param = params[0]; if (!param || param.seriesType !== 'candlestick') return ''; var values = klineValues[param.dataIndex]; var date = new Date(param.name); var formattedTime = ('0' + date.getHours()).slice(-2) + ':' + ('0' + date.getMinutes()).slice(-2); var formattedDate = (date.getMonth() + 1) + '/' + date.getDate(); return `${param.seriesName}<br/>时间: ${formattedDate} ${formattedTime}<br/>` + `<strong>开盘:</strong> ${values[0]}<br/><strong>收盘:</strong> ${values[1]}<br/>` + `<strong>最低:</strong> ${values[2]}<br/><strong>最高:</strong> ${values[3]}`; } },
        legend: {
            data: ['K线', 'MA5', 'MA10', 'MA30'],
            inactiveColor: '#777',
            textStyle: { color: '#e0e0e0' },
            bottom: isMobile ? '45px' : '40px'
        },
        grid: gridOption,
        xAxis: { type: 'category', data: dates, scale: true, boundaryGap: false, axisLine: { onZero: false }, splitLine: { show: false }, min: 'dataMin', max: 'dataMax', axisLabel: { color: '#e0e0e0', formatter: (v) => new Date(v).toLocaleTimeString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) } },
        yAxis: { scale: true, splitArea: { show: false }, axisLabel: { color: '#e0e0e0' }, splitLine: { lineStyle: { color: '#444' } } },
        dataZoom: dataZoomOption,
        series: [
            { type: 'candlestick', name: 'K线', data: klineValues, itemStyle: { color: '#ef232a', color0: '#14b143', borderColor: '#ef232a', borderColor0: '#14b143' }, markLine: { symbol: 'none', data: avgCostLine } },
            { name: 'MA5', type: 'line', data: ma5Data, smooth: true, showSymbol: false, lineStyle: { opacity: 0.8, color: '#FFFFFF', width: 1.0 } },
            { name: 'MA10', type: 'line', data: ma10Data, smooth: true, showSymbol: false, lineStyle: { opacity: 0.8, color: '#ff60ff', width: 1.0 } },
            { name: 'MA30', type: 'line', data: ma30Data, smooth: true, showSymbol: false, lineStyle: { opacity: 0.8, color: '#ffd700', width: 1.0 } }
        ]
    };
    if (myChart) myChart.setOption(option, true);
}

// --- 数据获取与股票切换 ---
async function switchStock(stockId) {
    const stock = allStocks.find(s => s.stock_id === stockId);
    if (!stock) return;
    document.querySelectorAll('.tab[data-stock-id]').forEach(tab => { tab.classList.toggle('active', tab.dataset.stockId === stockId); });
    window.location.hash = stockId;
    if (isLoggedIn) { document.getElementById('trade-panel-title').innerText = `交易: ${stock.name} (${stock.stock_id})`; }

    const cacheKey = `${currentUserHashForKline}_${stockId}_${currentPeriod}`;
    if (klineDataCache[cacheKey]) {
        renderChart(stock.name, stockId, klineDataCache[cacheKey]);
        return;
    }

    const fetchUrl = `/api/kline/${stockId}?period=${currentPeriod}&user_hash=${currentUserHashForKline}`;
    if (myChart) myChart.showLoading();
    try {
        const response = await fetch(fetchUrl);
        if (!response.ok) throw new Error('Network response was not ok');
        const responseData = await response.json();
        klineDataCache[cacheKey] = responseData;
        renderChart(stock.name, stockId, responseData);
    } catch (error) {
        console.error('Failed to fetch kline data:', error);
        const padding = 49; 
        const fetchUrl = `/api/kline/${stockId}?period=${currentPeriod}&user_hash=${currentUserHashForKline}&padding=${padding}`;        
        if (myChart) myChart.showLoading({ text: '数据加载失败' });
    } finally {
        if (myChart) myChart.hideLoading();
    }
}

// --- UI & 辅助函数 ---
function showToast(message, type = 'info') { const container = document.getElementById('toast-container'); const toast = document.createElement('div'); toast.className = `toast ${type}`; toast.textContent = message; container.appendChild(toast); setTimeout(() => { toast.classList.add('show'); }, 10); setTimeout(() => { toast.classList.remove('show'); setTimeout(() => { if (container.contains(toast)) { container.removeChild(toast); } }, 500); }, 3000); }
function openModal(modalId) { document.getElementById(modalId).style.display = 'flex'; }
function closeModal(modalId) { document.getElementById(modalId).style.display = 'none'; }

function updateUIForAuthState() {
    const loginBtn = document.getElementById('login-btn');
    const registerBtn = document.getElementById('register-btn');
    const logoutBtn = document.getElementById('logout-btn');
    const getTokenBtn = document.getElementById('get-token-btn'); // 新增
    const userInfoDisp = document.getElementById('user-info-display');
    const tradePanel = document.getElementById('trade-panel');
    const tradePanelTitle = document.getElementById('trade-panel-title');
    if (isLoggedIn) {
        loginBtn.style.display = 'none';
        registerBtn.style.display = 'none';
        logoutBtn.style.display = 'inline-block';
        getTokenBtn.style.display = 'inline-block'; // 新增：登录后显示按钮
        const loginId = localStorage.getItem('loginId') || currentUserId;
        userInfoDisp.innerText = `欢迎, ${loginId}`;
        tradePanel.classList.add('active');
        const currentStockId = window.location.hash.substring(1);
        if (currentStockId) {
            const stock = allStocks.find(s => s.stock_id === currentStockId);
            tradePanelTitle.innerText = stock ? `交易: ${stock.name} (${stock.stock_id})` : '请选择一支股票进行交易';
        } else {
            tradePanelTitle.innerText = '请选择一支股票进行交易';
        }
    } else {
        loginBtn.style.display = 'inline-block';
        registerBtn.style.display = 'inline-block';
        logoutBtn.style.display = 'none';
        getTokenBtn.style.display = 'none'; // 新增：未登录时隐藏按钮
        userInfoDisp.innerText = '访客模式';
        tradePanel.classList.remove('active');
        tradePanelTitle.innerText = '请先登录以进行交易';
    }
}

// --- 认证与交易逻辑 ---
async function handleRegister() { const userId = document.getElementById('reg-userid').value.trim(); const password = document.getElementById('reg-password').value; if (!userId || !password) { showToast('登录名和密码不能为空', 'error'); return; } try { const response = await fetch('/api/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId, password: password }) }); const result = await response.json(); if (response.ok) { const verificationCode = result.verification_code; const registerModalContent = document.querySelector('#register-modal .modal-content'); registerModalContent.innerHTML = `<span class="close-button" onclick="closeModal('register-modal')">&times;</span><h2>请验证您的QQ身份</h2><p style="text-align: center; font-size: 16px;">请将以下6位验证码通过QQ发送给机器人:</p><p style="font-size: 32px; font-weight: bold; color: #a6e3a1; text-align: center; letter-spacing: 5px; margin: 20px 0;">${verificationCode}</p><p style="text-align: center; color: #888; font-size: 14px;">格式为：<br> <code style="background:#333; padding: 2px 5px; border-radius:3px;">/验证 ${verificationCode}</code></p><p style="text-align: center; color: #888; font-size: 12px;">(验证码5分钟内有效)</p>`; showToast('验证码已生成，请查收！', 'success'); } else { throw new Error(result.error || '发起注册失败'); } } catch (error) { showToast(error.message, 'error'); } }

async function handleLogin() {
    const userId = document.getElementById('login-userid').value.trim();
    const password = document.getElementById('login-password').value;
    if (!userId || !password) { showToast('用户ID和密码不能为空', 'error'); return; }
    try {
        const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId, password: password }) });
        const result = await response.json();
        if (response.ok && result.access_token) {
            authToken = result.access_token;
            currentUserId = result.user_id;
            isLoggedIn = true;
            localStorage.setItem('authToken', authToken);
            localStorage.setItem('currentUserId', currentUserId);
            localStorage.setItem('loginId', result.login_id);

            try {
                const hashResponse = await fetch(`/api/get_user_hash?qq_id=${currentUserId}`);
                const hashData = await hashResponse.json();
                if (hashData.user_hash) {
                    currentUserHashForKline = hashData.user_hash;
                    localStorage.setItem('currentUserHashForKline', currentUserHashForKline);
                }
            } catch (e) {
                showToast('获取用户哈希失败，成本线可能无法显示', 'error');
            }

            Object.keys(klineDataCache).forEach(key => delete klineDataCache[key]);
            showToast('登录成功！', 'success');
            closeModal('login-modal');
            await refreshPortfolioData();
            updateUIForAuthState();
            const currentStockId = window.location.hash.substring(1);
            if (currentStockId) { switchStock(currentStockId); }
        } else { throw new Error(result.error || '登录失败'); }
    } catch (error) { showToast(error.message, 'error'); }
}

function handleLogout() {
    localStorage.clear();
    authToken = null; currentUserId = null; isLoggedIn = false; currentUserHashForKline = initialUserHash;
    showToast('已退出登录。');
    window.location.href = window.location.pathname;
}

// 获取并显示Token的函数
async function handleGetMyToken() {
    if (!isLoggedIn || !authToken) {
        showToast('请先登录', 'error');
        return;
    }
    try {
        const response = await fetch('/api/auth/me/token', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        const result = await response.json();
        if (response.ok && result.access_token) {
            document.getElementById('api-token-display').value = result.access_token;
            openModal('token-modal');
        } else {
            throw new Error(result.error || '获取Token失败');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function copyTokenToClipboard() {
    const tokenTextarea = document.getElementById('api-token-display');
    tokenTextarea.select();
    tokenTextarea.setSelectionRange(0, 99999); // For mobile devices
    try {
        document.execCommand('copy');
        showToast('Token已成功复制到剪贴板！', 'success');
    } catch (err) {
        showToast('复制失败，请手动复制', 'error');
    }
    closeModal('token-modal');
}

async function handleTrade(type) { if (!isLoggedIn) { showToast('请先登录再进行交易', 'error'); openModal('login-modal'); return; } const stockId = window.location.hash.substring(1); const quantity = parseInt(document.getElementById('trade-quantity').value, 10); if (!stockId) { showToast('请先选择一支股票', 'error'); return; } if (isNaN(quantity) || quantity <= 0) { showToast('请输入有效的交易数量', 'error'); return; } const stockName = allStocks.find(s => s.stock_id === stockId)?.name || stockId; if (!confirm(`您确定要【${type === 'buy' ? '买入' : '卖出'}】 ${quantity} 股 ${stockName} 吗？`)) { return; } const endpoint = `/api/v1/trade/${type}`; try { const response = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}` }, body: JSON.stringify({ stock_id: stockId, quantity: quantity }) }); const result = await response.json(); if (response.ok && result.success) { showToast(result.message, 'success'); document.getElementById('trade-quantity').value = ''; await refreshPortfolioData(); } else { throw new Error(result.message || result.error || '交易失败'); } } catch (error) { showToast(error.message, 'error'); } }

async function refreshPortfolioData() { if (!isLoggedIn || !currentUserId) return; const wrapper = document.getElementById('portfolio-wrapper'); try { const response = await fetch(`/api/v1/portfolio`, { headers: { 'Authorization': `Bearer ${authToken}` } }); if (!response.ok) throw new Error('获取持仓数据失败'); const data = await response.json(); let html = `<div class="portfolio-card"><div class="portfolio-header"><span class="name">${data.user_name}</span><span class="label">的持仓详情</span></div>`; if (data.holdings_detailed && data.holdings_detailed.length > 0) { data.holdings_detailed.forEach(stock => { const pnlClass = stock.pnl >= 0 ? 'positive' : 'negative'; html += `<div class="portfolio-stock-item clickable-stock" data-stock-id="${stock.stock_id}"><div class="portfolio-stock-header"><span class="stock-name">${stock.name} (${stock.stock_id})</span><span class="market-value">市值 $${stock.market_value.toFixed(2)}</span></div><div class="portfolio-stock-details"><span>${stock.quantity}股 @ $${stock.avg_cost.toFixed(2)}</span><span class="portfolio-pnl ${pnlClass}">盈亏: $${stock.pnl.toFixed(2)} (${stock.pnl_percent.toFixed(2)}%)</span></div></div>`; }); const totalPNL = data.holdings_detailed.reduce((sum, h) => sum + h.pnl, 0); const totalCost = data.stock_value - totalPNL; const totalPNLPercent = totalCost !== 0 ? (totalPNL / totalCost) * 100 : 0; const totalPNLClass = totalPNL >= 0 ? 'positive' : 'negative'; html += `<div class="portfolio-footer"><div class="portfolio-footer-item"><span>总市值</span><span>$${data.stock_value.toFixed(2)}</span></div><div class="portfolio-footer-item"><span>总盈亏</span><span class="portfolio-pnl ${totalPNLClass}">$${totalPNL.toFixed(2)} (${totalPNLPercent.toFixed(2)}%)</span></div></div>`; } else { html += '<div class="no-holdings-message">哎呀，你当前没有持仓呢！</div>'; } html += '</div>'; wrapper.innerHTML = html; wrapper.querySelectorAll('.clickable-stock').forEach(item => { item.addEventListener('click', () => switchStock(item.dataset.stockId)); }); } catch (error) { console.error("刷新持仓失败:", error); wrapper.innerHTML = '<div class="no-holdings-message">刷新持仓数据失败</div>'; } }

function checkLoginStatus() {
    const token = localStorage.getItem('authToken');
    const userId = localStorage.getItem('currentUserId');
    const userHash = localStorage.getItem('currentUserHashForKline');
    if (token && userId && userHash) {
        authToken = token;
        currentUserId = userId;
        currentUserHashForKline = userHash;
        isLoggedIn = true;
        refreshPortfolioData();
    }
    updateUIForAuthState();
}

function openForgotPasswordModal() { closeModal('login-modal'); openModal('forgot-password-modal'); }

async function handleForgotPasswordRequest() {
    const userId = document.getElementById('forgot-userid').value.trim();
    if (!userId) { showToast('请输入您的登录ID', 'error'); return; }
    try {
        const response = await fetch('/api/auth/forgot-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: userId }) });
        const result = await response.json();
        if (response.ok && result.success) {
            const resetCode = result.reset_code;
            const modalContent = document.getElementById('forgot-password-content');
            modalContent.innerHTML = `<span class="close-button" onclick="closeModal('forgot-password-modal')">&times;</span><h2>请验证您的QQ身份</h2><p>请将以下6位重置码通过您绑定的QQ号发送给机器人:</p><p style="font-size: 32px; font-weight: bold; color: #a6e3a1; text-align: center; letter-spacing: 5px; margin: 20px 0;">${resetCode}</p><p style="text-align: center; color: #888; font-size: 14px;">格式为：<br> <code style="background:#333; padding: 2px 5px; border-radius:3px;">/重置密码 ${resetCode}</code></p><hr style="border-color: #444; margin: 20px 0;"><p>验证成功后，请在此处输入您的新密码:</p><div class="modal-form-group"><label for="reset-new-password-input">新密码</label><input type="password" id="reset-new-password-input" placeholder="请输入新密码"></div><button class="modal-button" onclick="handleResetPassword('${userId}', '${resetCode}')">确认修改</button>`;
            showToast('重置码已生成，请查收！', 'success');
        } else { throw new Error(result.error || '该用户不存在或未绑定QQ'); }
    } catch (error) { showToast(error.message, 'error'); }
}

async function handleResetPassword(loginId, resetCode) {
    const newPassword = document.getElementById('reset-new-password-input').value;
    if (!newPassword) { showToast('新密码不能为空', 'error'); return; }
    try {
        const response = await fetch('/api/auth/reset-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_id: loginId, reset_code: resetCode, new_password: newPassword }) });
        const result = await response.json();
        if (response.ok && result.success) {
            showToast('密码重置成功！现在可以使用新密码登录了。', 'success');
            const modalContent = document.getElementById('forgot-password-content');
            modalContent.innerHTML = `<span class="close-button" onclick="closeModal('forgot-password-modal')">&times;</span><h2>重置密码</h2><div class="modal-form-group"><label for="forgot-userid">登录ID</label><input type="text" id="forgot-userid" placeholder="请输入您要重置密码的登录ID"></div><button class="modal-button" onclick="handleForgotPasswordRequest()">获取重置码</button>`;
            closeModal('forgot-password-modal');
            openModal('login-modal');
        } else { throw new Error(result.error || '密码重置失败'); }
    } catch (error) { showToast(error.message, 'error'); }
}

// --- 初始化 ---
document.addEventListener('DOMContentLoaded', () => {
    const scriptTag = document.getElementById('main-script');
    initialUserHash = scriptTag.dataset.userHash;
    allStocks = JSON.parse(scriptTag.dataset.stocks);
    currentUserHashForKline = initialUserHash;

    myChart = echarts.init(document.getElementById('kline-chart'), 'dark');
    document.querySelectorAll('.tab[data-stock-id]').forEach(tab => { tab.addEventListener('click', () => switchStock(tab.dataset.stockId)); });
    document.querySelectorAll('.time-tab').forEach(tab => { tab.addEventListener('click', () => { if (tab.classList.contains('active')) return; currentPeriod = tab.dataset.period; document.querySelectorAll('.time-tab').forEach(t => t.classList.remove('active')); tab.classList.add('active'); const stockId = window.location.hash.substring(1) || (allStocks.length > 0 ? allStocks[0].stock_id : null); if (stockId) { Object.keys(klineDataCache).forEach(key => delete klineDataCache[key]); switchStock(stockId); } }); });
    document.querySelectorAll('.portfolio-stock-item').forEach(item => { item.addEventListener('click', () => switchStock(item.dataset.stockId)); });
    
    const initialStockId = window.location.hash.substring(1) || (allStocks.length > 0 ? allStocks[0].stock_id : null);
    checkLoginStatus();
    if (initialStockId) { switchStock(initialStockId); }
    
    setInterval(() => { const stockId = window.location.hash.substring(1); if (stockId && document.hasFocus()) { const cacheKey = `${currentUserHashForKline}_${stockId}_${currentPeriod}`; delete klineDataCache[cacheKey]; switchStock(stockId); } }, 2.5 * 60 * 1000);
});

window.onmousedown = function (event) {
    // 只有当鼠标直接在灰色背景（.modal元素）上按下时，才关闭弹窗
    if (event.target.classList.contains('modal')) {
        closeModal(event.target.id);
    }
}