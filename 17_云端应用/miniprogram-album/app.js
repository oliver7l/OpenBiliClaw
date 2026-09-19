// 乐仔相册 · 全局数据层
//
// 数据由 scripts/build_album_miniprogram.py 生成到 data/photos.js（含媒体签名，
// 不入库）。这里做两件事：
//   1. 把「按月分组」摊平成一维数组，方便分页加载与查看器用下标定位；
//   2. 预先拼好**完整可访问 URL**（base + 路径 + 媒体签名）——小程序 <image>
//      带不了 cookie，签名只能走 URL 查询参数，页面直接用不再拼接。
//
// 媒体签名由服务端 session_secret 派生（见 openbiliclaw.auth_core
// .album_media_token）：换掉 session_secret 就能一次性作废所有已分发的链接。
const album = require('./data/photos.js');

const flat = [];
const groups = [];

album.months.forEach((month, gi) => {
  const start = flat.length;
  month.items.forEach((item) => {
    const thumbName = item[0];
    const fullPath = item[1];
    const date = item[2];
    flat.push({
      m: month.label,
      d: date,
      t: album.base + '/album/thumbs/' + thumbName + '?k=' + album.key,
      f: album.base + fullPath + '?k=' + album.key,
    });
  });
  groups.push({
    gi,
    label: month.label,
    meta: month.meta,
    count: month.items.length,
    start,
    end: flat.length,
  });
});

App({
  globalData: {
    base: album.base,
    generated: album.generated,
    total: album.total,
    flat,
    groups,
  },
});
