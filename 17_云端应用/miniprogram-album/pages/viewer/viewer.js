// 乐仔相册 · 大图查看
//
// 只渲染**一张**图（不用 swiper）：本项目单月最多 ~1900 张，swiper 会把所有
// item 都建出来，必卡。改为单图 + 手势/点击切换，并预取相邻两张。
const app = getApp();

Page({
  data: {
    url: '',
    date: '',
    pos: 0,
    count: 0,
    label: '',
    loading: false,
    barVisible: true,
  },

  onLoad(options) {
    this.flat = app.globalData.flat;
    const groups = app.globalData.groups;
    const grp = groups[Number(options.gi || 0)] || groups[0];
    this.start = grp.start;
    this.end = grp.end;
    this.setData({ label: grp.label, count: grp.end - grp.start });
    this.show(Number(options.i || grp.start));
  },

  show(i) {
    if (i < this.start) {
      i = this.start;
    }
    if (i >= this.end) {
      i = this.end - 1;
    }
    this.cur = i;
    const p = this.flat[i];
    this.setData({
      url: p.f,
      date: p.d || '未标注日期',
      pos: i - this.start + 1,
      loading: true,
    });
    this.preloadAround(i);
  },

  /** 预取相邻两张，滑动时才不会看到白屏 */
  preloadAround(i) {
    const self = this;
    [i + 1, i - 1].forEach(function (k) {
      if (k >= self.start && k < self.end) {
        wx.getImageInfo({ src: self.flat[k].f, fail: function () {} });
      }
    });
  },

  prev() {
    if (this.cur > this.start) {
      this.show(this.cur - 1);
    } else {
      this.toast('已经是第一张');
    }
  },

  next() {
    if (this.cur < this.end - 1) {
      this.show(this.cur + 1);
    } else {
      this.toast('已经是最后一张');
    }
  },

  onTouchStart(e) {
    const t = e.touches[0];
    this.tx = t.clientX;
    this.ty = t.clientY;
  },

  onTouchEnd(e) {
    const t = e.changedTouches[0];
    const dx = t.clientX - this.tx;
    const dy = t.clientY - this.ty;
    // 横向意图明显的滑动才翻页，避免和纵向手势打架
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) {
        this.next();
      } else {
        this.prev();
      }
    }
  },

  onTap(e) {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const x = (e.detail && e.detail.x) || 0;
    if (x < info.windowWidth * 0.33) {
      this.prev();
    } else if (x > info.windowWidth * 0.67) {
      this.next();
    } else {
      this.setData({ barVisible: !this.data.barVisible });
    }
  },

  onImgLoad() {
    this.setData({ loading: false });
  },

  onImgError() {
    this.setData({ loading: false });
    this.toast('这张图加载失败');
  },

  onSave() {
    const p = this.flat[this.cur];
    const self = this;
    wx.showLoading({ title: '保存中…', mask: true });
    wx.downloadFile({
      url: p.f,
      success(res) {
        if (res.statusCode !== 200) {
          wx.hideLoading();
          self.toast('下载失败 ' + res.statusCode);
          return;
        }
        wx.saveImageToPhotosAlbum({
          filePath: res.tempFilePath,
          success() {
            wx.hideLoading();
            wx.showToast({ title: '已存到相册' });
          },
          fail(err) {
            wx.hideLoading();
            const msg = String((err && err.errMsg) || '');
            if (msg.indexOf('auth') >= 0 || msg.indexOf('authorize') >= 0) {
              wx.showModal({
                title: '需要相册权限',
                content: '请在设置里允许「保存到相册」后重试。',
                confirmText: '去设置',
                success(r) {
                  if (r.confirm) {
                    wx.openSetting();
                  }
                },
              });
            } else {
              self.toast('保存失败');
            }
          },
        });
      },
      fail() {
        wx.hideLoading();
        self.toast('下载失败');
      },
    });
  },

  toast(title) {
    wx.showToast({ title, icon: 'none' });
  },
});
