"""豆瓣书影音模块：书影音清单的独立存储、API 与内容源。

通过独立 ``douban.db``（``[storage] douban_db_path``）存放用户从豆瓣抓取的
影视/书/音乐（看过/想看/在看）清单，提供 ``/api/douban*`` 后端接口与
``/web/douban`` 桌面 tab；可选注册为 ``douban`` 内容源（默认关闭）。
"""

from __future__ import annotations

from openbiliclaw.douban.service import DoubanService
from openbiliclaw.douban.store import DoubanStore

__all__ = ["DoubanStore", "DoubanService"]
