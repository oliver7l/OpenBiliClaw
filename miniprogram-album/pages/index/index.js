// 乐仔相册 · 相册墙
//
// 性能约定（很重要）：本项目有 5205 张照片，小程序**不能**一次性渲染全部
// image 节点（会直接卡死，且 setData 单次上限 1MB）。所以这里做两件事：
//   1. 按月份切换视图（一次只渲染一个月的照片，单月最多 ~1900 张）；
//   2. 月内分页加载，且只用**路径增量** setData
//      （``list[12] = {...}``），避免每页都把已渲染的几千条重新传一遍。
const app = getApp();

const PAGE_SIZE = 60; // 3 列 × 20 行

Page({
  data: {
    months: [],
    activeGi: 0,
    curLabel: '',
    list: [],
    shown: 0,
    monthTotal: 0,
    total: 0,
    done: false,
    generated: '',
  },

  onLoad() {
    const g = app.globalData;
    this.flat = g.flat;
    this.allGroups = g.groups;
    this.setData({
      months: g.groups.map((x) => ({ gi: x.gi, label: x.label, count: x.count })),
      total: g.total,
      generated: g.generated,
    });
    this.selectMonth(0);
  },

  /** 切换月份：重置列表（默认从最新月份开始，索引 0 即最近） */
  selectMonth(gi) {
    this.cur = this.allGroups[gi];
    this.setData({
      activeGi: gi,
      curLabel: this.cur.label,
      list: [],
      shown: 0,
      done: false,
      monthTotal: this.cur.count,
    });
    this.loadMore();
  },

  onMonthTap(e) {
    const gi = Number(e.currentTarget.dataset.gi);
    if (gi === this.data.activeGi) {
      wx.pageScrollTo({ scrollTop: 0, duration: 200 });
      return;
    }
    this.selectMonth(gi);
    wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  },

  /** 追加下一页（路径增量 setData） */
  loadMore() {
    if (this.data.done || !this.cur) {
      return;
    }
    const from = this.cur.start + this.data.shown;
    const to = Math.min(this.cur.end, from + PAGE_SIZE);
    if (to <= from) {
      this.setData({ done: true });
      return;
    }

    const base = this.data.list.length;
    const patches = {
      shown: this.data.shown + (to - from),
      done: to >= this.cur.end,
    };
    for (let i = from; i < to; i++) {
      const p = this.flat[i];
      patches['list[' + (base + i - from) + ']'] = { i, t: p.t, d: p.d };
    }
    this.setData(patches);
  },

  onReachBottom() {
    this.loadMore();
  },

  onOpen(e) {
    const i = Number(e.currentTarget.dataset.i);
    wx.navigateTo({ url: '/pages/viewer/viewer?gi=' + this.data.activeGi + '&i=' + i });
  },
});
