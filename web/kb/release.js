/* Independent from catalog loading: a graph snapshot failure must not hide installs. */
(() => {
  'use strict';
  const command = document.getElementById('install-command');
  const copy = document.getElementById('copy-install');
  const version = document.getElementById('release-version');
  const status = document.getElementById('install-status');
  const releases = 'https://code.alipay.com/pd.pt/dogpdkb/releases';
  const versionPattern = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;

  function resizeCommand() {
    command.style.height = 'auto';
    command.style.height = `${Math.max(68, command.scrollHeight)}px`;
  }

  // Metadata is an instruction visitors paste into a shell. Accept only our exact
  // HTTPS release origin and safe tag shapes; never render arbitrary shell text.
  function validate(data) {
    if (!data || data.schema_version !== 1 || data.product !== 'dogpdkb' ||
        data.provider !== 'antcode' || data.auth !== 'none' ||
        typeof data.version !== 'string' || typeof data.tag !== 'string' ||
        !versionPattern.test(data.version)) return false;
    if (!['stable', 'preview'].includes(data.channel)) return false;
    const validTag = data.channel === 'stable'
      ? data.tag === `v${data.version}`
      : /^installer-uat-[0-9]{8}T[0-9]{6}Z$/.test(data.tag);
    return validTag && data.release_url === releases &&
      data.installer_url === `${releases}/download/${data.tag}/install.sh`;
  }

  copy.addEventListener('click', async () => {
    if (!command.value || copy.disabled) return;
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(command.value);
        copied = true;
      }
    } catch { /* HTTP deployments and denied clipboard permissions use selection. */ }
    if (!copied) {
      command.focus();
      command.select();
      command.setSelectionRange(0, command.value.length);
      try { copied = document.execCommand('copy'); } catch { /* Manual copy remains possible. */ }
    }
    if (copied) {
      copy.textContent = '已复制 ✓';
      status.textContent = '已复制。在 Linux 服务器终端粘贴执行即可。';
    } else {
      status.textContent = '命令已选中，请按 Ctrl+C（或长按）复制。';
    }
  });

  window.addEventListener('resize', resizeCommand);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  fetch('../releases/dogpdkb.json', { cache: 'no-store', signal: controller.signal })
    .then((response) => {
      if (!response.ok) throw new Error('Release metadata unavailable');
      return response.json();
    })
    .then((data) => {
      if (!validate(data)) throw new Error('Invalid release metadata');
      command.value = `curl -fsSL ${data.installer_url} | bash`;
      const preview = data.channel === 'preview';
      version.textContent = `${preview ? '预览版' : '稳定版'} v${data.version}`;
      version.classList.toggle('preview', preview);
      status.textContent = preview
        ? '当前为已验证的 UAT 预览包，非正式稳定版。'
        : '复制命令，在 Linux 服务器上安装或升级此版本。';
      copy.disabled = false;
      resizeCommand();
    })
    .catch(() => {
      version.textContent = '版本信息暂不可用';
      command.value = '';
      command.placeholder = '请前往 AntCode Releases 查看可用版本。';
      status.textContent = '暂时无法确认安装版本；为避免误装，已停用复制。';
      copy.disabled = true;
    })
    .finally(() => clearTimeout(timer));
})();
