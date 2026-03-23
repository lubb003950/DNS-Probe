/* DNS 探测系统 — 全局共用脚本 */

/* ── Picker（穿梭框）共用函数 ──────────────────── */
function pickerMoveRight(availId, selId) {
    const avail = document.getElementById(availId);
    const sel   = document.getElementById(selId);
    Array.from(avail.selectedOptions).forEach(opt => {
        if (!Array.from(sel.options).some(o => o.value === opt.value)) {
            sel.add(new Option(opt.text, opt.value));
        }
        avail.remove(opt.index);
    });
}
function pickerMoveLeft(availId, selId) {
    const avail = document.getElementById(availId);
    const sel   = document.getElementById(selId);
    Array.from(sel.selectedOptions).forEach(opt => {
        avail.add(new Option(opt.text, opt.value));
        sel.remove(opt.index);
    });
}
function dnsMoveRight(id)  { pickerMoveRight('dns-available-' + id, 'dns-selected-' + id); }
function dnsMoveLeft(id)   { pickerMoveLeft('dns-available-'  + id, 'dns-selected-' + id); }
function nodeMoveRight(id) { pickerMoveRight('node-available-' + id, 'node-selected-node-' + id); }
function nodeMoveLeft(id)  { pickerMoveLeft('node-available-'  + id, 'node-selected-node-' + id); }

function dnsPickerBeforeSubmit(id, form) {
    const sel   = document.getElementById('dns-selected-' + id);
    const errEl = document.getElementById('dns-picker-' + id + '-error');
    if (sel.options.length === 0) {
        errEl.style.display = 'block';
        return false;
    }
    errEl.style.display = 'none';
    /* 写入 DNS 隐藏字段 */
    form.querySelectorAll('input[name="dns_server_ids"]').forEach(el => el.remove());
    Array.from(sel.options).forEach(opt => {
        const inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = 'dns_server_ids'; inp.value = opt.value;
        form.appendChild(inp);
    });
    /* 写入节点隐藏字段 */
    const nodeSel = document.getElementById('node-selected-node-' + id);
    if (nodeSel) {
        form.querySelectorAll('input[name="node_ids"]').forEach(el => el.remove());
        Array.from(nodeSel.options).forEach(opt => {
            const inp = document.createElement('input');
            inp.type = 'hidden'; inp.name = 'node_ids'; inp.value = opt.value;
            form.appendChild(inp);
        });
    }
    return true;
}

/* ── 移动端侧边栏切换 ─────────────────────────── */
document.addEventListener('DOMContentLoaded', function () {
    const toggle  = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (!toggle || !sidebar || !overlay) return;

    function openSidebar() {
        sidebar.classList.add('sidebar-open');
        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
    function closeSidebar() {
        sidebar.classList.remove('sidebar-open');
        overlay.classList.remove('active');
        document.body.style.overflow = '';
    }
    toggle.addEventListener('click', function () {
        sidebar.classList.contains('sidebar-open') ? closeSidebar() : openSidebar();
    });
    overlay.addEventListener('click', closeSidebar);
});
