/**
 * 宝贝成长册「旧 app → 新 app」数据导出脚本
 *
 * 使用方法（3 步，~1 分钟）：
 *   1) 在浏览器打开 https://class-album.app.workbuddy.host/ 并登录你的账号
 *   2) 按 F12 打开开发者工具，切换到 Console 面板
 *   3) 把本文件全文复制粘贴进去，回车；脚本会自动下载一个 .json 文件
 *
 * 然后打开 https://class-album-99010.app.workbuddy.host/#import 登录/注册新账号
 * → 上传那个 JSON → 完成迁移
 *
 * 注意：脚本只读取你自己的数据，不会修改任何东西。导出后随时可以删除文件。
 */
(async () => {
  const log = (...a) => console.log('%c[宝贝成长册·导出]', 'color:#FF6B35;font-weight:bold', ...a);

  const c = window.cloud;
  if (!c) {
    log('FATAL: 当前页签的 cloud 未初始化。请先在「https://class-album.app.workbuddy.host/」登录后再粘贴运行。');
    return;
  }

  try {
    log('开始导出…');

    /* ===== 1. 读 kids + moments ===== */
    const { data: kids, error: e1 } = await c.database.from('kids').select('*');
    if (e1) { log('读 kids 失败:', e1); return; }
    const { data: moments, error: e2 } = await c.database.from('moments').select('*');
    if (e2) { log('读 moments 失败:', e2); return; }
    log(`宝贝 ${kids.length} 个，记录 ${moments.length} 条`);

    /* ===== 2. 收集所有照片路径（去重） ===== */
    const allPaths = [...new Set(moments.flatMap(m => m.photo_paths || []))];
    log(`照片 ${allPaths.length} 张（含去重），准备逐张下载…`);

    /* ===== 3. 逐张拉取签名 URL 并转 base64 ===== */
    const photoMap = {};
    for (let i = 0; i < allPaths.length; i++) {
      const p = allPaths[i];
      try {
        const urlsResp = await c.storage.createSignedUrls([p], 60);
        const u = urlsResp?.data?.[0]?.signedUrl || urlsResp?.[0]?.signedUrl || urlsResp?.[0]?.data?.signedUrl;
        if (!u) throw new Error('签名 URL 为空');
        const resp = await fetch(u);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        const b64 = await new Promise((resolve, reject) => {
          const fr = new FileReader();
          fr.onload = () => resolve(fr.result);
          fr.onerror = reject;
          fr.readAsDataURL(blob);
        });
        photoMap[p] = b64;
      } catch(e) {
        console.warn('照片下载失败，跳过:', p, e);
      }
      if ((i+1) % 5 === 0 || i === allPaths.length - 1) {
        log(`  照片进度：${i+1}/${allPaths.length}`);
      }
    }

    /* ===== 4. kid_ids 转换成 kid_names（kid id 是 app 私有的，不能直接搬） ===== */
    const kidById = Object.fromEntries(kids.map(k => [k.id, k]));
    const exportData = {
      schema_version: 1,
      exported_at: new Date().toISOString(),
      source: 'class-album.app.workbuddy.host',
      kids: kids.map(k => ({
        name: k.name, nickname: k.nickname || null, birthdate: k.birthdate || null,
      })),
      moments: moments.map(m => ({
        happened_on: m.happened_on,
        note: m.note || null,
        kid_names: (m.kid_ids || []).map(id => {
          const k = kidById[id];
          return k ? (k.nickname || k.name) : null;
        }).filter(Boolean),
        photo_paths: m.photo_paths || [],
      })),
      photo_b64: photoMap,
    };

    /* ===== 5. 触发下载 ===== */
    const json = JSON.stringify(exportData, null, 2);
    const sizeMB = (json.length / 1024 / 1024).toFixed(2);
    log(`JSON 大小：${sizeMB} MB`);

    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `class-album-export-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);

    log(`✅ 导出完成！已下载 JSON（${exportData.kids.length} 宝贝 / ${exportData.moments.length} 记录 / ${Object.keys(photoMap).length} 照片）`);
    log('下一步：打开 https://class-album-99010.app.workbuddy.host/#import  登录后上传这个 JSON');
  } catch(e) {
    log('导出过程中出错：', e);
  }
})();