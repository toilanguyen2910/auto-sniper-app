/**
 * ⚡ Auto-Sniper Single-Page Application Client
 * ES6+ Vanilla JavaScript Architecture
 * - Real-time WebSocket streaming & state management
 * - Web Audio API celebration arpeggio synthesis
 * - Preset auto-fill and dual-trigger execution
 * - Multi-modal visual pulse and toast alerts
 */

// ==========================================
// 1. APPLICATION STATE
// ==========================================
const state = {
    tasks: [],
    presets: {},
    activeTerminalTaskId: null,
    terminalWs: null,
    globalWs: null,
    autoScrollLogs: true,
    audioEnabled: localStorage.getItem('auto_sniper_audio') !== 'false',
    terminalLogLines: [],
    editingTaskId: null,
    deleteTaskId: null,
    reconnectTimer: null,
    stats: {
        total: 0,
        polling: 0,
        waiting: 0,
        sniping: 0,
        success: 0,
        stopped: 0
    }
};

// ==========================================
// 2. WEB AUDIO API SYNTHESIZER
// ==========================================
class WebAudioSynth {
    constructor() {
        this.ctx = null;
    }

    _ensureContext() {
        if (!this.ctx) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (AudioContextClass) {
                this.ctx = new AudioContextClass();
            }
        }
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
        return this.ctx;
    }

    /**
     * Synthesize triumphant 4-note celebration arpeggio:
     * C5 (523.25Hz), E5 (659.25Hz), G5 (783.99Hz), C6 (1046.50Hz)
     * Zero external audio file dependencies!
     */
    playSuccessArpeggio() {
        if (!state.audioEnabled) return;
        try {
            const ctx = this._ensureContext();
            if (!ctx) return;

            const notes = [
                { freq: 523.25, timeOffset: 0.00, duration: 0.28 }, // C5
                { freq: 659.25, timeOffset: 0.12, duration: 0.28 }, // E5
                { freq: 783.99, timeOffset: 0.24, duration: 0.32 }, // G5
                { freq: 1046.50, timeOffset: 0.38, duration: 0.65 } // C6
            ];

            const now = ctx.currentTime;

            notes.forEach(note => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();

                // Warm triangle harmonic waveform
                osc.type = 'triangle';
                osc.frequency.setValueAtTime(note.freq, now + note.timeOffset);

                // ADSR envelope: Fast attack, exponential decay
                const startTime = now + note.timeOffset;
                const endTime = startTime + note.duration;

                gain.gain.setValueAtTime(0.001, startTime);
                gain.gain.linearRampToValueAtTime(0.35, startTime + 0.03);
                gain.gain.exponentialRampToValueAtTime(0.0001, endTime);

                osc.connect(gain);
                gain.connect(ctx.destination);

                osc.start(startTime);
                osc.stop(endTime);
            });
        } catch (err) {
            console.warn('[AudioSynth] Error playing celebration arpeggio:', err);
        }
    }

    playTestBeep() {
        try {
            const ctx = this._ensureContext();
            if (!ctx) return;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            const now = ctx.currentTime;

            osc.type = 'sine';
            osc.frequency.setValueAtTime(880, now); // A5

            gain.gain.setValueAtTime(0.001, now);
            gain.gain.linearRampToValueAtTime(0.2, now + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);

            osc.connect(gain);
            gain.connect(ctx.destination);

            osc.start(now);
            osc.stop(now + 0.2);
        } catch (e) {
            console.warn('[AudioSynth] Test beep error:', e);
        }
    }
}

const synth = new WebAudioSynth();

// ==========================================
// 3. TOAST NOTIFICATIONS & VISUAL FX
// ==========================================
const Toast = {
    show(message, type = 'info', title = '') {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = 'pointer-events-auto flex items-start space-x-3 p-4 rounded-xl border shadow-2xl transition-all duration-300 transform translate-x-5 opacity-0 text-xs sm:text-sm';

        let bgBorder = 'bg-slate-900/95 border-slate-700 text-slate-100';
        let icon = 'ℹ️';

        if (type === 'success') {
            bgBorder = 'bg-emerald-950/95 border-emerald-500 text-emerald-100 shadow-[0_0_20px_rgba(16,185,129,0.3)]';
            icon = '🎉';
        } else if (type === 'error') {
            bgBorder = 'bg-rose-950/95 border-rose-500 text-rose-100 shadow-[0_0_20px_rgba(244,63,94,0.3)]';
            icon = '❌';
        } else if (type === 'warning') {
            bgBorder = 'bg-amber-950/95 border-amber-500 text-amber-100 shadow-[0_0_20px_rgba(245,158,11,0.3)]';
            icon = '⚠️';
        }

        toast.className += ` ${bgBorder}`;
        toast.innerHTML = `
            <span class="text-base select-none mt-0.5">${icon}</span>
            <div class="flex-1 min-w-0">
                ${title ? `<div class="font-bold text-white text-xs uppercase tracking-wide mb-0.5">${escapeHtml(title)}</div>` : ''}
                <div class="leading-relaxed break-words">${escapeHtml(message)}</div>
            </div>
            <button class="text-slate-400 hover:text-white p-1 rounded transition select-none">&times;</button>
        `;

        const closeBtn = toast.querySelector('button');
        const dismiss = () => {
            toast.classList.add('opacity-0', 'translate-x-5');
            setTimeout(() => toast.remove(), 300);
        };
        closeBtn.onclick = dismiss;

        container.appendChild(toast);

        // Slide in
        requestAnimationFrame(() => {
            toast.classList.remove('translate-x-5', 'opacity-0');
        });

        // Auto dismiss after 4 seconds
        setTimeout(dismiss, 4000);
    }
};

function triggerScreenSuccessFlash() {
    const glow = document.getElementById('screen-glow');
    if (!glow) return;
    glow.className = 'pointer-events-none fixed inset-0 z-40 transition-opacity duration-300 bg-emerald-500/10 shadow-[inset_0_0_100px_rgba(16,185,129,0.6)] opacity-100 celebration-pulse';
    setTimeout(() => {
        glow.className = 'pointer-events-none fixed inset-0 z-40 transition-opacity duration-700 opacity-0';
    }, 1200);
}

// Request desktop notification permissions
if ('Notification' in window && Notification.permission === 'default') {
    window.addEventListener('click', () => {
        if (Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }, { once: true });
}

function sendDesktopNotification(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
        try {
            new Notification(title, { body, icon: '/static/favicon.ico' });
        } catch (e) {
            // Ignore notification error
        }
    }
}

// ==========================================
// 4. REST API CLIENT
// ==========================================
const API = {
    async request(url, options = {}) {
        try {
            const resp = await fetch(url, {
                headers: {
                    'Content-Type': 'application/json',
                    ...options.headers
                },
                ...options
            });
            if (!resp.ok) {
                const errData = await resp.json().catch(() => ({ detail: resp.statusText }));
                throw new Error(errData.detail || `HTTP ${resp.status}`);
            }
            return await resp.json();
        } catch (err) {
            console.error(`API Error [${options.method || 'GET'} ${url}]:`, err);
            throw err;
        }
    },

    getHealth() {
        return this.request('/api/health');
    },

    getTasks() {
        return this.request('/api/tasks');
    },

    getPresets() {
        return this.request('/api/presets');
    },

    createTask(data) {
        return this.request('/api/tasks', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },

    updateTask(taskId, data) {
        return this.request(`/api/tasks/${taskId}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },

    deleteTask(taskId) {
        return this.request(`/api/tasks/${taskId}`, {
            method: 'DELETE'
        });
    },

    startTask(taskId) {
        return this.request(`/api/tasks/${taskId}/start`, {
            method: 'POST'
        });
    },

    stopTask(taskId) {
        return this.request(`/api/tasks/${taskId}/stop`, {
            method: 'POST'
        });
    },

    stopAll() {
        return this.request('/api/tasks/stop-all', {
            method: 'POST'
        });
    },

    confirmLogin(taskId) {
        return this.request(`/api/tasks/${taskId}/confirm_login`, {
            method: 'POST'
        });
    }
};

// ==========================================
// 5. WEBSOCKET SUBSCRIPTION CONTROLLER
// ==========================================
function getWsUrl(path) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host || '127.0.0.1:8000';
    return `${proto}//${host}${path}`;
}

function initGlobalWebSocket() {
    if (state.globalWs) {
        try { state.globalWs.close(); } catch (e) {}
    }

    const wsUrl = getWsUrl('/ws/logs');
    const ws = new WebSocket(wsUrl);
    state.globalWs = ws;

    const dot = document.getElementById('connection-dot');
    const text = document.getElementById('connection-text');

    ws.onopen = () => {
        if (dot) dot.className = 'h-2 w-2 rounded-full bg-emerald-400 animate-pulse';
        if (text) {
            text.textContent = 'Connected';
            text.className = 'text-emerald-300';
        }
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleGlobalWsMessage(data);
        } catch (e) {
            console.warn('Failed to parse WS message:', e);
        }
    };

    ws.onclose = () => {
        if (dot) dot.className = 'h-2 w-2 rounded-full bg-rose-500';
        if (text) {
            text.textContent = 'Reconnecting...';
            text.className = 'text-rose-400';
        }
        // Auto-reconnect with 3s backoff
        if (!state.reconnectTimer) {
            state.reconnectTimer = setTimeout(() => {
                state.reconnectTimer = null;
                initGlobalWebSocket();
            }, 3000);
        }
    };

    ws.onerror = () => {
        try { ws.close(); } catch (e) {}
    };
}

function handleGlobalWsMessage(data) {
    if (data.type === 'status_change') {
        const { task_id, status: newStatus, message } = data;
        const task = state.tasks.find(t => t.id === task_id);
        if (task) {
            const oldStatus = task.status;
            task.status = newStatus;
            task.updated_at = new Date().toISOString();

            // Trigger multi-modal alerts on SUCCESS transition
            if (newStatus === 'SUCCESS' && oldStatus !== 'SUCCESS') {
                synth.playSuccessArpeggio();
                triggerScreenSuccessFlash();
                Toast.show(`🎉 [${task.name}] Slot sniped and registered successfully!`, 'success', 'SUCCESS ALERT');
                sendDesktopNotification('⚡ Auto-Sniper Success!', `[${task.name}] Slot sniped and registered successfully!`);
            } else if (newStatus === 'WAITING_LOGIN') {
                Toast.show(`👁 [${task.name}] Waiting for user login / Captcha solve!`, 'warning', 'ACTION REQUIRED');
            } else if (newStatus === 'FAILED') {
                Toast.show(`❌ [${task.name}] Task execution failed: ${message || ''}`, 'error', 'TASK FAILED');
            }

            renderTasksGrid();
            updateStats();
        } else {
            // Task might have been added on another tab/session, reload
            loadTasks();
        }

        // Also update terminal header badge if currently inspecting this task
        if (state.activeTerminalTaskId === task_id) {
            const termStatus = document.getElementById('terminal-task-status');
            if (termStatus) {
                termStatus.textContent = newStatus;
                termStatus.className = `px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${getStatusBadgeClass(newStatus)}`;
            }
        }
    } else if (data.type === 'log') {
        // Global log stream
        if (data.level === 'SUCCESS') {
            synth.playSuccessArpeggio();
            triggerScreenSuccessFlash();
        }
    }
}

// ==========================================
// 6. TERMINAL WEBSOCKET STREAMING
// ==========================================
function openTerminal(taskId) {
    const task = state.tasks.find(t => t.id === taskId);
    if (!task) return;

    state.activeTerminalTaskId = taskId;
    state.terminalLogLines = [];

    const modal = document.getElementById('terminal-modal');
    const nameEl = document.getElementById('terminal-task-name');
    const idEl = document.getElementById('terminal-task-id');
    const statusEl = document.getElementById('terminal-task-status');
    const logsContainer = document.getElementById('terminal-logs');

    if (nameEl) nameEl.textContent = task.name;
    if (idEl) idEl.textContent = `ID: ${task.id}`;
    if (statusEl) {
        statusEl.textContent = task.status;
        statusEl.className = `px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${getStatusBadgeClass(task.status)}`;
    }
    if (logsContainer) {
        logsContainer.innerHTML = '<div class="text-slate-500 italic">// Connecting to live log buffer &amp; replaying history...</div>';
    }

    if (modal) modal.classList.remove('hidden');

    // Connect dedicated task WebSocket
    connectTerminalWs(taskId);
}

function closeTerminal() {
    const modal = document.getElementById('terminal-modal');
    if (modal) modal.classList.add('hidden');

    state.activeTerminalTaskId = null;
    if (state.terminalWs) {
        try {
            state.terminalWs.close();
        } catch (e) {}
        state.terminalWs = null;
    }
}

function connectTerminalWs(taskId) {
    if (state.terminalWs) {
        try { state.terminalWs.close(); } catch (e) {}
    }

    const wsUrl = getWsUrl(`/ws/tasks/${taskId}/logs`);
    const ws = new WebSocket(wsUrl);
    state.terminalWs = ws;

    const logsContainer = document.getElementById('terminal-logs');
    const stateEl = document.getElementById('terminal-ws-state');
    const lineCountEl = document.getElementById('terminal-line-count');

    let isFirstMessage = true;

    ws.onopen = () => {
        if (stateEl) stateEl.textContent = 'Live Connected';
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (isFirstMessage && logsContainer) {
                logsContainer.innerHTML = '';
                isFirstMessage = false;
            }
            appendLogLine(data);
        } catch (e) {
            console.warn('Terminal log parse error:', e);
        }
    };

    ws.onclose = () => {
        if (stateEl) stateEl.textContent = 'Disconnected';
        // Auto-reconnect if terminal modal is still viewing this task
        if (state.activeTerminalTaskId === taskId) {
            setTimeout(() => {
                if (state.activeTerminalTaskId === taskId) {
                    connectTerminalWs(taskId);
                }
            }, 2500);
        }
    };

    ws.onerror = () => {
        if (stateEl) stateEl.textContent = 'Connection Error';
    };
}

function appendLogLine(item) {
    const logsContainer = document.getElementById('terminal-logs');
    const lineCountEl = document.getElementById('terminal-line-count');
    if (!logsContainer) return;

    const time = item.timestamp || new Date().toLocaleTimeString();
    const level = (item.level || (item.type === 'status_change' ? 'STATUS' : 'INFO')).toUpperCase();
    const message = item.message || '';

    // Level-based styling
    let levelClass = 'text-sky-400';
    let icon = 'ℹ';
    if (level === 'WARNING' || level === 'WARN') {
        levelClass = 'text-amber-400';
        icon = '⚡';
    } else if (level === 'ERROR' || level === 'CRITICAL') {
        levelClass = 'text-rose-400 font-bold';
        icon = '✖';
    } else if (level === 'SUCCESS') {
        levelClass = 'text-emerald-400 font-bold';
        icon = '🎉';
    } else if (level === 'STATUS') {
        levelClass = 'text-cyan-400 font-semibold';
        icon = '🔄';
    }

    const row = document.createElement('div');
    row.className = 'flex items-start space-x-2 py-0.5 hover:bg-slate-900/60 rounded px-1 -mx-1';
    row.innerHTML = `
        <span class="text-slate-500 select-none shrink-0 font-mono">[${escapeHtml(time)}]</span>
        <span class="${levelClass} select-none shrink-0 font-mono">[${icon} ${escapeHtml(level)}]</span>
        <span class="text-slate-200 break-all flex-1">${escapeHtml(message)}</span>
    `;

    logsContainer.appendChild(row);

    // Save plain text line for clipboard copy
    state.terminalLogLines.push(`[${time}] [${level}] ${message}`);

    if (lineCountEl) {
        lineCountEl.textContent = `${state.terminalLogLines.length} log lines`;
    }

    if (state.autoScrollLogs) {
        logsContainer.scrollTop = logsContainer.scrollHeight;
    }
}

// ==========================================
// 7. TASK GRID & CARD RENDERING
// ==========================================
function getStatusBadgeClass(status) {
    switch (status) {
        case 'PENDING':
            return 'bg-slate-800 text-slate-300 border border-slate-700';
        case 'WAITING_LOGIN':
            return 'bg-amber-950/80 text-amber-300 border border-amber-500/60 animate-pulse ring-1 ring-amber-500/30';
        case 'POLLING':
            return 'bg-blue-950/80 text-blue-300 border border-blue-500/60 animate-pulse ring-1 ring-blue-500/30';
        case 'SNIPING':
            return 'bg-purple-950 text-purple-300 border border-purple-500 animate-pulse ring-2 ring-purple-500/40';
        case 'SUCCESS':
            return 'bg-emerald-950 text-emerald-300 border border-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.5)] ring-2 ring-emerald-400 animate-glow-success';
        case 'FAILED':
            return 'bg-rose-950 text-rose-300 border border-rose-500/50';
        case 'STOPPED':
        default:
            return 'bg-slate-900 text-slate-400 border border-slate-800';
    }
}

function getStatusIcon(status) {
    switch (status) {
        case 'PENDING': return '⏳';
        case 'WAITING_LOGIN': return '👁';
        case 'POLLING': return '🔄';
        case 'SNIPING': return '⚡';
        case 'SUCCESS': return '🎉';
        case 'FAILED': return '❌';
        case 'STOPPED': default: return '⏹';
    }
}

function renderTasksGrid() {
    const grid = document.getElementById('tasks-grid');
    const emptyState = document.getElementById('empty-state');
    if (!grid) return;

    if (!state.tasks || state.tasks.length === 0) {
        grid.innerHTML = '';
        if (emptyState) emptyState.classList.remove('hidden');
        return;
    }

    if (emptyState) emptyState.classList.add('hidden');
    grid.innerHTML = '';

    state.tasks.forEach(task => {
        const card = document.createElement('div');
        const isSuccess = task.status === 'SUCCESS';
        const isWaiting = task.status === 'WAITING_LOGIN';
        const isRunning = ['POLLING', 'SNIPING', 'WAITING_LOGIN', 'PENDING'].includes(task.status);

        card.className = `bg-slate-900/90 border rounded-2xl p-5 flex flex-col justify-between transition-all duration-300 shadow-xl relative overflow-hidden group ${
            isSuccess 
                ? 'border-emerald-500/70 shadow-[0_0_25px_rgba(16,185,129,0.25)] ring-1 ring-emerald-500/30' 
                : isWaiting 
                    ? 'border-amber-500/60 ring-1 ring-amber-500/20' 
                    : 'border-slate-800 hover:border-slate-700 hover:shadow-2xl'
        }`;

        // Top decorative accent glow
        let accentGlow = 'bg-slate-700/20';
        if (isSuccess) accentGlow = 'bg-emerald-500/20';
        else if (isWaiting) accentGlow = 'bg-amber-500/20';
        else if (task.status === 'POLLING') accentGlow = 'bg-blue-500/20';
        else if (task.status === 'SNIPING') accentGlow = 'bg-purple-500/20';

        const statusBadgeClass = getStatusBadgeClass(task.status);
        const statusIcon = getStatusIcon(task.status);

        // Credentials display
        const username = task.credentials?.username;
        const credsText = username 
            ? `👤 ${escapeHtml(username)} <span class="text-slate-500 text-[11px]">(••••••••)</span>` 
            : '<span class="text-slate-500">🔓 No Auth Required</span>';

        // Selectors summary
        const slotSelector = task.selectors?.slot_selector || 'None';
        const slotKeyword = task.selectors?.slot_keyword || '';

        card.innerHTML = `
            <div class="absolute -right-12 -top-12 w-28 h-28 rounded-full ${accentGlow} blur-2xl pointer-events-none"></div>

            <!-- Card Header -->
            <div class="space-y-2 mb-4">
                <div class="flex items-start justify-between gap-2">
                    <h3 class="font-bold text-white text-base tracking-tight truncate flex-1 title-tooltip" title="${escapeHtml(task.name)}">
                        ${escapeHtml(task.name)}
                    </h3>
                    <span class="px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider shrink-0 flex items-center space-x-1 ${statusBadgeClass}">
                        <span>${statusIcon}</span>
                        <span>${task.status}</span>
                    </span>
                </div>

                <!-- Target URL -->
                <div class="flex items-center space-x-2 text-xs text-slate-400">
                    <span class="truncate font-mono bg-slate-950 px-2 py-1 rounded border border-slate-800 flex-1 select-all" title="${escapeHtml(task.target_url)}">
                        ${escapeHtml(task.target_url)}
                    </span>
                    <button class="btn-copy-url p-1 hover:text-white rounded hover:bg-slate-800 transition text-slate-400" title="Copy Target URL" data-url="${escapeHtml(task.target_url)}">
                        📋
                    </button>
                </div>
            </div>

            <!-- Card Metadata Badges -->
            <div class="grid grid-cols-2 gap-2 text-xs mb-4">
                <div class="bg-slate-950/70 p-2.5 rounded-xl border border-slate-800/80">
                    <span class="text-[10px] text-slate-500 block uppercase font-semibold">Polling Rate</span>
                    <span class="font-mono text-slate-200 font-medium">⏱ ${task.polling_interval}s</span>
                </div>
                <div class="bg-slate-950/70 p-2.5 rounded-xl border border-slate-800/80">
                    <span class="text-[10px] text-slate-500 block uppercase font-semibold">Browser Mode</span>
                    <span class="font-medium ${task.headless ? 'text-slate-300' : 'text-amber-400'}">
                        ${task.headless ? '🖥 Headless' : '👁 Visible'}
                    </span>
                </div>
            </div>

            <!-- Credentials & Selector Info -->
            <div class="space-y-1.5 text-xs text-slate-400 bg-slate-950/40 p-3 rounded-xl border border-slate-800/60 mb-5 font-mono">
                <div class="flex items-center justify-between">
                    <span class="text-[10px] text-slate-500 uppercase font-semibold">Credentials:</span>
                    <div class="truncate max-w-[170px]">${credsText}</div>
                </div>
                <div class="flex items-center justify-between">
                    <span class="text-[10px] text-slate-500 uppercase font-semibold">Trigger Slot:</span>
                    <span class="text-slate-300 truncate max-w-[170px]" title="${escapeHtml(slotSelector)}">${escapeHtml(slotSelector)}</span>
                </div>
                ${task.enable_dual_trigger ? `
                <div class="flex items-center justify-between text-emerald-400 pt-1 border-t border-slate-800/60">
                    <span class="text-[10px] uppercase font-semibold">Dual-Trigger:</span>
                    <span class="text-[11px] font-bold">⚡ Active (API+UI)</span>
                </div>` : ''}
            </div>

            <!-- Card Actions -->
            <div class="space-y-2 pt-2 border-t border-slate-800/80">
                ${isWaiting ? `
                <!-- Confirm Login Action for WAITING_LOGIN -->
                <button type="button" class="btn-confirm-login w-full py-2 px-3 rounded-lg text-xs font-bold bg-amber-500 hover:bg-amber-400 text-slate-950 shadow-lg shadow-amber-500/20 flex items-center justify-center space-x-1.5 transition animate-pulse" data-id="${task.id}">
                    <span>🔑</span>
                    <span>Confirm Manual Login</span>
                </button>
                ` : ''}

                <div class="grid grid-cols-2 gap-2">
                    ${isRunning ? `
                    <!-- Stop Button -->
                    <button type="button" class="btn-stop-task py-2 px-3 rounded-lg text-xs font-bold bg-slate-800 hover:bg-rose-950/80 hover:text-rose-300 hover:border-rose-700/60 border border-slate-700 text-slate-200 transition flex items-center justify-center space-x-1" data-id="${task.id}">
                        <span>⏹</span>
                        <span>Stop</span>
                    </button>
                    ` : `
                    <!-- Start Button -->
                    <button type="button" class="btn-start-task py-2 px-3 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-slate-950 transition flex items-center justify-center space-x-1 shadow-md shadow-emerald-600/20" data-id="${task.id}">
                        <span>▶</span>
                        <span>Start</span>
                    </button>
                    `}

                    <!-- Live Terminal Logs Button -->
                    <button type="button" class="btn-logs-task py-2 px-3 rounded-lg text-xs font-bold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition flex items-center justify-center space-x-1" data-id="${task.id}">
                        <span>💻</span>
                        <span>Logs</span>
                    </button>
                </div>

                <div class="flex items-center justify-end space-x-2 pt-1 text-xs">
                    <!-- Edit Button -->
                    <button type="button" class="btn-edit-task text-slate-400 hover:text-white px-2 py-1 rounded hover:bg-slate-800 transition ${isRunning ? 'opacity-50 cursor-not-allowed' : ''}" data-id="${task.id}" ${isRunning ? 'disabled title="Cannot edit running task"' : 'title="Edit task parameters"'}>
                        ✏️ Edit
                    </button>
                    <!-- Delete Button -->
                    <button type="button" class="btn-delete-task text-slate-500 hover:text-rose-400 px-2 py-1 rounded hover:bg-rose-950/40 transition" data-id="${task.id}" title="Delete task permanently">
                        🗑️ Delete
                    </button>
                </div>
            </div>
        `;

        // Attach listeners for card actions
        const copyBtn = card.querySelector('.btn-copy-url');
        if (copyBtn) {
            copyBtn.onclick = () => {
                navigator.clipboard.writeText(copyBtn.dataset.url).then(() => {
                    Toast.show('Target URL copied to clipboard!', 'info');
                });
            };
        }

        const startBtn = card.querySelector('.btn-start-task');
        if (startBtn) {
            startBtn.onclick = () => handleStartTask(task.id);
        }

        const stopBtn = card.querySelector('.btn-stop-task');
        if (stopBtn) {
            stopBtn.onclick = () => handleStopTask(task.id);
        }

        const confirmBtn = card.querySelector('.btn-confirm-login');
        if (confirmBtn) {
            confirmBtn.onclick = () => handleConfirmLogin(task.id);
        }

        const logsBtn = card.querySelector('.btn-logs-task');
        if (logsBtn) {
            logsBtn.onclick = () => openTerminal(task.id);
        }

        const editBtn = card.querySelector('.btn-edit-task');
        if (editBtn && !isRunning) {
            editBtn.onclick = () => openEditModal(task.id);
        }

        const deleteBtn = card.querySelector('.btn-delete-task');
        if (deleteBtn) {
            deleteBtn.onclick = () => openDeleteModal(task.id, task.name);
        }

        grid.appendChild(card);
    });
}

function updateStats() {
    const total = state.tasks.length;
    let polling = 0;
    let waiting = 0;
    let sniping = 0;
    let success = 0;
    let stopped = 0;

    state.tasks.forEach(t => {
        if (t.status === 'POLLING') polling++;
        else if (t.status === 'WAITING_LOGIN') waiting++;
        else if (t.status === 'SNIPING') sniping++;
        else if (t.status === 'SUCCESS') success++;
        else stopped++;
    });

    state.stats = { total, polling, waiting, sniping, success, stopped };

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setVal('stat-total', total);
    setVal('stat-polling', polling);
    setVal('stat-waiting', waiting);
    setVal('stat-sniping', sniping);
    setVal('stat-success', success);
    setVal('stat-stopped', stopped);
}

// ==========================================
// 8. TASK CREATION / EDITING MODAL
// ==========================================
function openCreateModal() {
    state.editingTaskId = null;
    const modal = document.getElementById('task-modal');
    const title = document.getElementById('modal-title');
    const form = document.getElementById('task-form');
    const presetSelector = document.getElementById('preset-selector');

    if (title) title.textContent = 'Create New Sniper Task';
    if (form) form.reset();
    document.getElementById('task-id').value = '';

    // Default preset
    if (presetSelector) {
        presetSelector.value = 'custom';
        applyPresetToForm('custom');
    }

    if (modal) modal.classList.remove('hidden');
}

function openEditModal(taskId) {
    const task = state.tasks.find(t => t.id === taskId);
    if (!task) return;

    state.editingTaskId = taskId;
    const modal = document.getElementById('task-modal');
    const title = document.getElementById('modal-title');
    const presetSelector = document.getElementById('preset-selector');

    if (title) title.textContent = `Edit Task: ${task.name}`;

    // Fill form fields
    document.getElementById('task-id').value = task.id;
    document.getElementById('task-name').value = task.name || '';
    document.getElementById('task-target-url').value = task.target_url || '';
    document.getElementById('task-polling-interval').value = task.polling_interval ?? 3.5;
    document.getElementById('task-jitter').value = task.jitter_sec ?? 0.5;
    document.getElementById('task-headless').checked = task.headless !== false;
    document.getElementById('task-manual-login').checked = task.manual_login === true;

    // Credentials
    document.getElementById('task-username').value = task.credentials?.username || '';
    document.getElementById('task-password').value = task.credentials?.password || '';

    // Selectors
    document.getElementById('task-slot-selector').value = task.selectors?.slot_selector || '';
    document.getElementById('task-slot-keyword').value = task.selectors?.slot_keyword || '';
    document.getElementById('task-full-keyword').value = task.selectors?.full_keyword || '';
    document.getElementById('task-ui-slot-selector').value = task.selectors?.ui_slot_selector || '';
    document.getElementById('task-ui-submit-selector').value = task.selectors?.ui_submit_selector || '';
    document.getElementById('task-modal-selector').value = task.selectors?.ui_confirm_modal_selector || '';

    // API Trigger
    document.getElementById('task-enable-dual-trigger').checked = task.enable_dual_trigger !== false;
    document.getElementById('task-api-endpoint').value = task.api_trigger?.api_endpoint || '';
    document.getElementById('task-api-method').value = task.api_trigger?.api_method || 'POST';

    const payload = task.api_trigger?.api_payload;
    document.getElementById('task-api-payload').value = payload ? JSON.stringify(payload, null, 2) : '';

    if (presetSelector) presetSelector.value = 'custom';
    updatePresetDescription('custom');

    if (modal) modal.classList.remove('hidden');
}

function closeTaskModal() {
    const modal = document.getElementById('task-modal');
    if (modal) modal.classList.add('hidden');
    state.editingTaskId = null;
}

function updatePresetDescription(presetId) {
    const descEl = document.getElementById('preset-description');
    if (!descEl) return;

    if (presetId === 'vau_events') {
        descEl.textContent = 'Canh và đăng ký vé sự kiện VAUVERSE / NRL VAU siêu tốc với cơ chế kích hoạt kép (Direct API & UI Fallback).';
    } else if (presetId === 'vau_course') {
        descEl.textContent = 'Canh và đăng ký học phần tín chỉ VAU qua hệ thống đào tạo với quy trình hỗ trợ đăng nhập và tự động tick môn học.';
    } else {
        descEl.textContent = 'Tùy chỉnh linh hoạt URL, tần số quét và bộ chọn DOM cho bất kỳ trang web mục tiêu nào.';
    }
}

function applyPresetToForm(presetId) {
    updatePresetDescription(presetId);

    const preset = state.presets[presetId];
    if (!preset) {
        if (presetId === 'custom') {
            // Keep or clear to blank defaults
            document.getElementById('task-name').value = 'Custom Sniper Task';
            document.getElementById('task-polling-interval').value = '3.5';
            document.getElementById('task-jitter').value = '0.5';
            document.getElementById('task-headless').checked = true;
            document.getElementById('task-manual-login').checked = false;
        }
        return;
    }

    // Auto-fill values from preset
    document.getElementById('task-name').value = preset.name;
    document.getElementById('task-target-url').value = preset.target_url;
    document.getElementById('task-polling-interval').value = preset.polling_interval ?? 3.5;
    document.getElementById('task-jitter').value = preset.jitter_sec ?? 0.5;
    document.getElementById('task-headless').checked = preset.headless ?? true;
    document.getElementById('task-manual-login').checked = preset.manual_login ?? false;

    // DOM selectors
    const sel = preset.selectors || {};
    document.getElementById('task-slot-selector').value = sel.slot_selector || '';
    document.getElementById('task-slot-keyword').value = sel.slot_keyword || '';
    document.getElementById('task-full-keyword').value = sel.full_keyword || '';
    document.getElementById('task-ui-slot-selector').value = sel.ui_slot_selector || '';
    document.getElementById('task-ui-submit-selector').value = sel.ui_submit_selector || '';
    document.getElementById('task-modal-selector').value = sel.ui_confirm_modal_selector || '';

    // API trigger
    const api = preset.api_trigger || {};
    document.getElementById('task-enable-dual-trigger').checked = preset.enable_dual_trigger !== false;
    document.getElementById('task-api-endpoint').value = api.api_endpoint || '';
    document.getElementById('task-api-method').value = api.api_method || 'POST';

    const payload = api.api_payload;
    document.getElementById('task-api-payload').value = payload ? JSON.stringify(payload, null, 2) : '';

    Toast.show(`Preset '${preset.name}' loaded into form!`, 'info');
}

async function handleSaveTask() {
    const form = document.getElementById('task-form');
    if (!form.reportValidity()) return;

    const taskId = document.getElementById('task-id').value;
    const name = document.getElementById('task-name').value.trim();
    const targetUrl = document.getElementById('task-target-url').value.trim();
    const pollingInterval = parseFloat(document.getElementById('task-polling-interval').value) || 3.5;
    const jitterSec = parseFloat(document.getElementById('task-jitter').value) || 0.5;
    const headless = document.getElementById('task-headless').checked;
    const manualLogin = document.getElementById('task-manual-login').checked;

    const username = document.getElementById('task-username').value.trim();
    const password = document.getElementById('task-password').value;

    const slotSelector = document.getElementById('task-slot-selector').value.trim();
    const slotKeyword = document.getElementById('task-slot-keyword').value.trim();
    const fullKeyword = document.getElementById('task-full-keyword').value.trim();
    const uiSlotSelector = document.getElementById('task-ui-slot-selector').value.trim();
    const uiSubmitSelector = document.getElementById('task-ui-submit-selector').value.trim();
    const modalSelector = document.getElementById('task-modal-selector').value.trim();

    const enableDualTrigger = document.getElementById('task-enable-dual-trigger').checked;
    const apiEndpoint = document.getElementById('task-api-endpoint').value.trim();
    const apiMethod = document.getElementById('task-api-method').value;
    const payloadStr = document.getElementById('task-api-payload').value.trim();

    let apiPayload = null;
    if (payloadStr) {
        try {
            apiPayload = JSON.parse(payloadStr);
        } catch (e) {
            Toast.show('JSON Payload syntax error: please verify JSON format.', 'error', 'INVALID PAYLOAD');
            return;
        }
    }

    const payload = {
        name,
        target_url: targetUrl,
        polling_interval: pollingInterval,
        jitter_sec: jitterSec,
        headless,
        manual_login: manualLogin,
        enable_dual_trigger: enableDualTrigger,
        credentials: {
            username: username || undefined,
            password: password || undefined
        },
        selectors: {
            slot_selector: slotSelector || undefined,
            slot_keyword: slotKeyword || undefined,
            full_keyword: fullKeyword || undefined,
            ui_slot_selector: uiSlotSelector || undefined,
            ui_submit_selector: uiSubmitSelector || undefined,
            ui_confirm_modal_selector: modalSelector || undefined
        },
        api_trigger: {
            api_endpoint: apiEndpoint || undefined,
            api_method: apiMethod,
            api_payload: apiPayload || undefined
        }
    };

    try {
        if (taskId) {
            // Update
            await API.updateTask(taskId, payload);
            Toast.show(`Task '${name}' updated successfully!`, 'success');
        } else {
            // Create
            await API.createTask(payload);
            Toast.show(`New task '${name}' created!`, 'success');
        }
        closeTaskModal();
        await loadTasks();
    } catch (err) {
        Toast.show(err.message || 'Failed to save task', 'error');
    }
}

// ==========================================
// 9. TASK LIFECYCLE HANDLERS
// ==========================================
async function handleStartTask(taskId) {
    try {
        await API.startTask(taskId);
        Toast.show('Task execution started!', 'info');
        await loadTasks();
    } catch (err) {
        Toast.show(err.message || 'Failed to start task', 'error');
    }
}

async function handleStopTask(taskId) {
    try {
        await API.stopTask(taskId);
        Toast.show('Task stopped.', 'info');
        await loadTasks();
    } catch (err) {
        Toast.show(err.message || 'Failed to stop task', 'error');
    }
}

async function handleConfirmLogin(taskId) {
    try {
        await API.confirmLogin(taskId);
        Toast.show('Manual login confirmed! Resuming automated high-speed polling...', 'success');
        await loadTasks();
    } catch (err) {
        Toast.show(err.message || 'Could not confirm manual login', 'error');
    }
}

async function handleStopAll() {
    try {
        const res = await API.stopAll();
        Toast.show(`Emergency Stop: ${res.stopped_count || 0} active tasks stopped!`, 'warning', 'STOPPED ALL');
        await loadTasks();
    } catch (err) {
        Toast.show(err.message || 'Failed to stop all tasks', 'error');
    }
}

function openDeleteModal(taskId, taskName) {
    state.deleteTaskId = taskId;
    const modal = document.getElementById('delete-modal');
    const nameEl = document.getElementById('delete-task-name');
    if (nameEl) nameEl.textContent = `"${taskName}"`;
    if (modal) modal.classList.remove('hidden');
}

function closeDeleteModal() {
    const modal = document.getElementById('delete-modal');
    if (modal) modal.classList.add('hidden');
    state.deleteTaskId = null;
}

async function handleConfirmDelete() {
    if (!state.deleteTaskId) return;
    const id = state.deleteTaskId;
    try {
        await API.deleteTask(id);
        Toast.show('Task permanently deleted.', 'info');
        closeDeleteModal();
        await loadTasks();
    } catch (err) {
        Toast.show(err.message || 'Failed to delete task', 'error');
    }
}

// ==========================================
// 10. DATA LOADING & INITIALIZATION
// ==========================================
async function loadPresets() {
    try {
        const presetsList = await API.getPresets();
        state.presets = {};
        presetsList.forEach(p => {
            state.presets[p.id] = p;
        });
    } catch (err) {
        console.warn('Could not load presets:', err);
    }
}

async function loadTasks() {
    try {
        const tasks = await API.getTasks();
        state.tasks = tasks;
        renderTasksGrid();
        updateStats();
    } catch (err) {
        console.warn('Could not load tasks:', err);
        Toast.show('Error connecting to backend API', 'error');
    }
}

function toggleAudio() {
    state.audioEnabled = !state.audioEnabled;
    localStorage.setItem('auto_sniper_audio', state.audioEnabled);

    const icon = document.getElementById('audio-icon');
    const label = document.getElementById('audio-label');
    const btn = document.getElementById('btn-toggle-audio');

    if (state.audioEnabled) {
        if (icon) icon.textContent = '🔊';
        if (label) label.textContent = 'Audio: ON';
        if (btn) btn.className = 'flex items-center space-x-1.5 px-2.5 py-1 rounded-md transition text-emerald-400 hover:text-emerald-300 hover:bg-slate-700/50';
        Toast.show('Celebration audio alert enabled!', 'info');
    } else {
        if (icon) icon.textContent = '🔇';
        if (label) label.textContent = 'Audio: OFF';
        if (btn) btn.className = 'flex items-center space-x-1.5 px-2.5 py-1 rounded-md transition text-slate-400 hover:text-slate-300 hover:bg-slate-700/50';
        Toast.show('Audio alert muted.', 'info');
    }
}

// Helper to escape HTML characters
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ==========================================
// 11. DOM EVENT ATTACHMENTS
// ==========================================
document.addEventListener('DOMContentLoaded', async () => {
    // 1. Initialize Audio toggle state
    const icon = document.getElementById('audio-icon');
    const label = document.getElementById('audio-label');
    const audioBtn = document.getElementById('btn-toggle-audio');
    if (!state.audioEnabled) {
        if (icon) icon.textContent = '🔇';
        if (label) label.textContent = 'Audio: OFF';
        if (audioBtn) audioBtn.className = 'flex items-center space-x-1.5 px-2.5 py-1 rounded-md transition text-slate-400 hover:text-slate-300 hover:bg-slate-700/50';
    }

    if (audioBtn) audioBtn.onclick = toggleAudio;

    const testSoundBtn = document.getElementById('btn-test-sound');
    if (testSoundBtn) {
        testSoundBtn.onclick = () => {
            synth.playSuccessArpeggio();
            triggerScreenSuccessFlash();
            Toast.show('Web Audio synth: 4-note celebration arpeggio played!', 'success');
        };
    }

    // 2. Navigation Actions
    const stopAllBtn = document.getElementById('btn-stop-all');
    if (stopAllBtn) stopAllBtn.onclick = handleStopAll;

    const createBtn = document.getElementById('btn-create-task');
    if (createBtn) createBtn.onclick = openCreateModal;

    const emptyCreateBtn = document.getElementById('btn-empty-create');
    if (emptyCreateBtn) emptyCreateBtn.onclick = openCreateModal;

    const refreshBtn = document.getElementById('btn-refresh-tasks');
    if (refreshBtn) refreshBtn.onclick = async () => {
        await loadTasks();
        Toast.show('Tasks refreshed from encrypted store', 'info');
    };

    // 3. Task Modal Controls
    const closeTaskModalBtn = document.getElementById('btn-close-task-modal');
    if (closeTaskModalBtn) closeTaskModalBtn.onclick = closeTaskModal;

    const cancelTaskBtn = document.getElementById('btn-cancel-task');
    if (cancelTaskBtn) cancelTaskBtn.onclick = closeTaskModal;

    const saveTaskBtn = document.getElementById('btn-save-task');
    if (saveTaskBtn) saveTaskBtn.onclick = handleSaveTask;

    const presetSelector = document.getElementById('preset-selector');
    if (presetSelector) {
        presetSelector.onchange = (e) => applyPresetToForm(e.target.value);
    }

    const togglePasswordBtn = document.getElementById('btn-toggle-password');
    if (togglePasswordBtn) {
        togglePasswordBtn.onclick = () => {
            const pwdInput = document.getElementById('task-password');
            if (pwdInput) {
                pwdInput.type = pwdInput.type === 'password' ? 'text' : 'password';
            }
        };
    }

    // 4. Terminal Modal Controls
    const closeTerminalBtn = document.getElementById('btn-close-terminal');
    if (closeTerminalBtn) closeTerminalBtn.onclick = closeTerminal;

    const clearTerminalBtn = document.getElementById('btn-clear-terminal');
    if (clearTerminalBtn) {
        clearTerminalBtn.onclick = () => {
            const container = document.getElementById('terminal-logs');
            if (container) container.innerHTML = '';
            state.terminalLogLines = [];
            const countEl = document.getElementById('terminal-line-count');
            if (countEl) countEl.textContent = '0 log lines';
        };
    }

    const copyTerminalBtn = document.getElementById('btn-copy-terminal');
    if (copyTerminalBtn) {
        copyTerminalBtn.onclick = () => {
            if (state.terminalLogLines.length === 0) {
                Toast.show('No logs to copy', 'warning');
                return;
            }
            const textToCopy = state.terminalLogLines.join('\n');
            navigator.clipboard.writeText(textToCopy).then(() => {
                Toast.show(`Copied ${state.terminalLogLines.length} log lines to clipboard!`, 'success');
            }).catch(() => {
                Toast.show('Could not access clipboard', 'error');
            });
        };
    }

    const autoScrollBtn = document.getElementById('btn-toggle-autoscroll');
    if (autoScrollBtn) {
        autoScrollBtn.onclick = () => {
            state.autoScrollLogs = !state.autoScrollLogs;
            if (state.autoScrollLogs) {
                autoScrollBtn.textContent = 'Auto-Scroll: ON';
                autoScrollBtn.className = 'px-2.5 py-1 rounded bg-emerald-950 text-emerald-400 border border-emerald-600/50 hover:bg-emerald-900/60 text-xs font-mono transition';
                const container = document.getElementById('terminal-logs');
                if (container) container.scrollTop = container.scrollHeight;
            } else {
                autoScrollBtn.textContent = 'Auto-Scroll: OFF';
                autoScrollBtn.className = 'px-2.5 py-1 rounded bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700 text-xs font-mono transition';
            }
        };
    }

    // 5. Delete Modal Controls
    const cancelDeleteBtn = document.getElementById('btn-cancel-delete');
    if (cancelDeleteBtn) cancelDeleteBtn.onclick = closeDeleteModal;

    const confirmDeleteBtn = document.getElementById('btn-confirm-delete');
    if (confirmDeleteBtn) confirmDeleteBtn.onclick = handleConfirmDelete;

    // 6. Global Keybindings
    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeTaskModal();
            closeTerminal();
            closeDeleteModal();
        }
    });

    // 7. Load Data & Start WebSockets
    await loadPresets();
    await loadTasks();
    initGlobalWebSocket();
});
